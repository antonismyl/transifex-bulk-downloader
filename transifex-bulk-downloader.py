#!/usr/bin/env python3
"""
Hybrid Transifex Bulk Downloader
Combines Python API discovery with official CLI downloads

Features:
- 🔍 Discovers ALL projects in an organization using Python API
- ⚙️ Generates .tx/config using official 'tx add remote' commands  
- 🚀 Downloads using optimized official 'tx pull' with worker pools
- 🛡️ Robust error handling with --skip for problematic resources
- 📊 Enhanced progress tracking with filtered output mode
- 🔄 Intelligent existing configuration analysis with resource counting
- 🔧 Automatic fixing of single quote issues in .tx/config files
- 📈 Comprehensive reporting with config age and new project detection

Version: 1.2 (Enhanced config analysis and quote handling)
"""

import subprocess
import sys
import os
import json
import time
import shutil
import getpass
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass
import tempfile
import logging
import re
from datetime import datetime
import threading

# Platform-specific imports
if sys.platform.startswith('win'):
    fcntl = None
    try:
        import select
    except ImportError:
        select = None
else:
    import fcntl
    import select

# Try to import required packages
try:
    from transifex.api import transifex_api
    from transifex.api.exceptions import DownloadException
    import requests
    from tqdm import tqdm
except ImportError as e:
    missing_pkg = str(e).split("'")[1] if "'" in str(e) else str(e)
    print(f"❌ Missing required package: {missing_pkg}")
    print("📦 Please install with: pip install transifex-python requests tqdm")
    sys.exit(1)

@dataclass
class DiscoveryConfig:
    """Configuration for the discovery and download process"""
    api_token: str
    organization_slug: str
    project_slugs: Optional[List[str]] = None
    output_directory: Optional[Path] = None
    download_mode: str = "both"  # source, translations, both
    language_codes: Optional[List[str]] = None
    workers: int = 12  # Optimal default balancing speed and API limits
    skip_on_error: bool = True
    use_silent_mode: bool = False
    use_filtered_output: bool = False
    file_filter: str = "<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>"
    minimum_perc: int = 0
    force_download: bool = False
    skip_existing_files: bool = True  # Don't re-download existing files
    add_remote_timeout: int = 300  # 5 minutes timeout for add remote
    
    @classmethod
    def load_from_file(cls, config_path: Path) -> 'DiscoveryConfig':
        """Load configuration from JSON file"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON in config file: {e}")
        except IOError as e:
            raise ValueError(f"Cannot read config file: {e}")

        return cls(
            api_token=data.get('api_token', ''),
            organization_slug=data.get('organization_slug', ''),
            project_slugs=data.get('project_slugs'),
            output_directory=Path(data['output_directory']) if data.get('output_directory') else None,
            download_mode=data.get('download_mode', 'both'),
            language_codes=data.get('language_codes'),
            workers=min(data.get('workers', 12), 30),  # CLI max is 30
            skip_on_error=data.get('skip_on_error', True),
            use_silent_mode=data.get('use_silent_mode', False),
            use_filtered_output=data.get('use_filtered_output', False),
            file_filter=data.get('file_filter', '<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>'),
            minimum_perc=data.get('minimum_perc', 0),
            force_download=data.get('force_download', False),
            skip_existing_files=data.get('skip_existing_files', True),
            add_remote_timeout=data.get('add_remote_timeout', 300)
        )

    def save_to_file(self, config_path: Path) -> None:
        """Save configuration to JSON file"""
        data = {
            'api_token': self.api_token if self.api_token else '*** SET YOUR TOKEN HERE ***',
            'organization_slug': self.organization_slug,
            'project_slugs': self.project_slugs,
            'output_directory': str(self.output_directory) if self.output_directory else None,
            'download_mode': self.download_mode,
            'language_codes': self.language_codes,
            'workers': self.workers,
            'skip_on_error': self.skip_on_error,
            'use_silent_mode': self.use_silent_mode,
            'use_filtered_output': self.use_filtered_output,
            'file_filter': self.file_filter,
            'minimum_perc': self.minimum_perc,
            'force_download': self.force_download,
            'skip_existing_files': self.skip_existing_files,
            'add_remote_timeout': self.add_remote_timeout,
            '_comments': {
                'download_mode': 'Options: source, translations, both',
                'language_codes': 'List of language codes for translations (null = all languages)',
                'workers': 'Number of concurrent downloads (max 30 for CLI)',
                'file_filter': 'Pattern for file organization. Use <project_slug>, <resource_slug>, <lang>, <ext>',
                'skip_on_error': 'Continue on individual resource failures',
                'use_filtered_output': 'Show custom progress bars with ETA and important errors only',
                'minimum_perc': 'Minimum translation completion percentage (0-100)',
                'skip_existing_files': 'Skip files that already exist locally (faster incremental downloads)',
                'add_remote_timeout': 'Timeout in seconds for tx add remote commands'
            }
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def validate_file_filter(self) -> bool:
        """Validate that file_filter contains required placeholders"""
        required_placeholders = ['<project_slug>', '<resource_slug>', '<lang>', '<ext>']
        for placeholder in required_placeholders:
            if placeholder not in self.file_filter:
                return False
        return True

class HybridTransifexDownloader:
    """Main class for hybrid discovery + CLI download approach"""
    
    def __init__(self, config: DiscoveryConfig):
        self.config = config
        self.organization = None
        self.logger = self._setup_logging()
        self._setup_api()
        
        # Verify CLI availability
        self._verify_cli_available()
        
        # Validate configuration
        if not self.config.validate_file_filter():
            raise ValueError(f"Invalid file_filter pattern. Must contain: <project_slug>, <resource_slug>, <lang>, <ext>")
        
    def _setup_logging(self) -> logging.Logger:
        """Setup logging with memory limits and proper cleanup"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        logger_name = f"hybrid_downloader_{timestamp}"
        
        # Remove any existing logger to prevent accumulation
        existing_logger = logging.getLogger(logger_name)
        for handler in existing_logger.handlers[:]:
            handler.close()
            existing_logger.removeHandler(handler)
        
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        
        # File handler with rotation and size limits
        log_file = Path(f"hybrid_download_{timestamp}.log")
        try:
            from logging.handlers import RotatingFileHandler
            # 10MB max file size, keep 2 backup files
            file_handler = RotatingFileHandler(
                log_file, maxBytes=10*1024*1024, backupCount=2, encoding='utf-8'
            )
        except ImportError:
            # Fallback to regular file handler
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
        
        # Store handler reference for cleanup
        self._log_handler = file_handler
        
        print(f"📝 Logging to: {log_file}")
        
        return logger
        
    def _setup_api(self) -> None:
        """Initialize Transifex API"""
        try:
            transifex_api.setup(auth=self.config.api_token)
        except Exception as e:
            raise ValueError(f"Failed to setup API with provided token: {e}")
            
    def _verify_cli_available(self) -> None:
        """Verify that Transifex CLI is available and working"""
        try:
            result = subprocess.run(['tx', '--version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                raise FileNotFoundError("CLI returned error")
            print(f"✅ Transifex CLI detected: {result.stdout.strip()}")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("❌ Transifex CLI not found or not working")
            print("📋 Please install the official Transifex CLI:")
            print("   curl -o- https://raw.githubusercontent.com/transifex/cli/master/install.sh | bash")
            print("   Or download from: https://github.com/transifex/cli/releases")
            sys.exit(1)
            
    def validate_token_and_org(self) -> None:
        """Validate API token and fetch organization"""
        print("🔑 Validating API token and organization...")
        
        try:
            # Get organization
            self.organization = transifex_api.Organization.get(slug=self.config.organization_slug)
            print(f"✅ Organization found: {self.organization.name}")
            
            # Test API access by getting first few projects
            projects_iter = self.organization.fetch("projects").all()
            project_count = 0
            for _ in projects_iter:
                project_count += 1
                if project_count >= 3:  # Just verify we can access projects
                    break
            print(f"✅ API access verified - organization has projects")
            
        except Exception as e:
            error_msg = str(e).lower()
            if "unauthorized" in error_msg or "403" in error_msg:
                raise ValueError("API token is invalid or has insufficient permissions")
            elif "not found" in error_msg or "404" in error_msg:
                raise ValueError(f"Organization '{self.config.organization_slug}' not found or not accessible")
            else:
                raise ValueError(f"API validation failed: {e}")
                
    def discover_projects(self) -> List[Dict]:
        """Discover all projects in the organization with memory-efficient iteration"""
        print("🔍 Discovering projects in organization...")
        
        try:
            projects_iterator = self.organization.fetch("projects").all()
            
            if self.config.project_slugs:
                # Filter to specific projects using iterator
                target_slugs = set(self.config.project_slugs)
                filtered_projects = []
                found_slugs = set()
                
                # Process projects one by one to avoid loading all into memory
                for project in projects_iterator:
                    if project.slug in target_slugs:
                        filtered_projects.append(project)
                        found_slugs.add(project.slug)
                        # Early exit if we found all requested projects
                        if len(found_slugs) == len(target_slugs):
                            break
                
                missing_slugs = target_slugs - found_slugs
                if missing_slugs:
                    print(f"⚠️  Projects not found: {', '.join(missing_slugs)}")
                
                print(f"📋 Found {len(filtered_projects)} of {len(self.config.project_slugs)} requested project(s)")
                return filtered_projects
            else:
                # Convert to list only when we need the count
                all_projects = list(projects_iterator)
                print(f"📋 Found {len(all_projects)} project(s) in organization")
                return all_projects
                
        except Exception as e:
            raise ValueError(f"Failed to discover projects: {e}")
    
    def parse_existing_config_enhanced(self, config_path: Path) -> Dict:
        """Enhanced parsing of .tx/config to extract resource and project information"""
        if not config_path.exists():
            return {
                'config_exists': False,
                'config_age_hours': None,
                'total_resources': 0,
                'projects_with_resources': {},
                'all_resources': [],
                'configured_projects': set()
            }
        
        # Get config file age
        try:
            config_stat = config_path.stat()
            config_modified = datetime.fromtimestamp(config_stat.st_mtime)
            age_hours = (datetime.now() - config_modified).total_seconds() / 3600
        except:
            age_hours = None
        
        all_resources = []
        projects_with_resources = {}
        configured_projects = set()
        
        try:
            # Memory-efficient: Stream file line by line instead of loading all into memory
            resource_pattern = re.compile(r'\[o:([^:]+):p:([^:]+):r:([^\]]+)\]')
            max_config_size = 50 * 1024 * 1024  # 50MB limit for config files
            
            # Check file size first
            if config_path.stat().st_size > max_config_size:
                self.logger.warning(f"Config file too large ({config_path.stat().st_size} bytes), processing line by line")
                
            with open(config_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    # Memory safety: Don't process extremely long lines
                    if len(line) > 10000:
                        continue
                        
                    resource_match = resource_pattern.search(line)
                    if resource_match:
                        org, project, resource = resource_match.groups()
                        if org == self.config.organization_slug:
                            all_resources.append({
                                'project_slug': project,
                                'resource_slug': resource,
                                'org_slug': org,
                                'full_section': f"o:{org}:p:{project}:r:{resource}"
                            })
                            configured_projects.add(project)
                            
                            # Count resources per project
                            if project not in projects_with_resources:
                                projects_with_resources[project] = 0
                            projects_with_resources[project] += 1
                    
                    # Memory safety: Don't process extremely large config files
                    if line_num > 1000000:  # 1M lines limit
                        self.logger.warning(f"Config file too large, stopping at line {line_num}")
                        break
            
            return {
                'config_exists': True,
                'config_age_hours': age_hours,
                'total_resources': len(all_resources),
                'projects_with_resources': projects_with_resources,
                'all_resources': all_resources,
                'configured_projects': configured_projects
            }
            
        except Exception as e:
            self.logger.warning(f"Could not parse existing config: {e}")
            return {
                'config_exists': True,
                'config_age_hours': age_hours,
                'total_resources': 0,
                'projects_with_resources': {},
                'all_resources': [],
                'configured_projects': set()
            }
    
    def check_existing_config_enhanced(self, work_dir: Path, projects: List) -> Tuple[bool, Dict, Set[str], Set[str]]:
        """Enhanced check of existing .tx/config with detailed analysis"""
        tx_config_path = work_dir / ".tx" / "config"
        
        # Parse existing configuration with enhanced analysis
        config_analysis = self.parse_existing_config_enhanced(tx_config_path)
        
        if not config_analysis['config_exists']:
            return False, config_analysis, set(), set()
        
        # Compare with discovered projects
        discovered_project_slugs = {p.slug for p in projects}
        configured_projects = config_analysis['configured_projects']
        
        if not configured_projects:
            return True, config_analysis, set(), set()
        
        # Find differences
        missing_projects = discovered_project_slugs - configured_projects  # Projects to be added
        extra_projects = configured_projects - discovered_project_slugs   # Projects in config but not discovered
        
        return True, config_analysis, missing_projects, extra_projects
    
    def generate_config_status_message(self, config_analysis: Dict, discovered_projects: List, missing_projects: Set[str]) -> str:
        """Generate human-readable status message about configuration"""
        lines = []
        total_discovered = len(discovered_projects)
        
        # Basic stats
        lines.append("📊 Configuration Status:")
        lines.append(f"   🗂️  Detected {config_analysis['total_resources']} resources from {len(config_analysis['projects_with_resources'])} projects")
        lines.append(f"   🏢 Your Transifex org has {total_discovered} total projects")
        
        # Age information
        if config_analysis['config_age_hours'] is not None:
            age_hours = config_analysis['config_age_hours']
            if age_hours < 1:
                age_str = f"{age_hours * 60:.0f} minutes"
            elif age_hours < 24:
                age_str = f"{age_hours:.1f} hours"
            else:
                age_str = f"{age_hours / 24:.1f} days"
            
            lines.append(f"   ⏰ Config is {age_str} old")
        
        # New projects
        if missing_projects:
            lines.append(f"   ✨ {len(missing_projects)} new/empty projects detected:")
            for project in sorted(list(missing_projects)[:5]):  # Show first 5
                lines.append(f"      + {project}")
            if len(missing_projects) > 5:
                lines.append(f"      ... and {len(missing_projects) - 5} more")
        
        # Resource breakdown (top projects)
        if config_analysis['projects_with_resources']:
            lines.append(f"   📋 Top projects by resource count:")
            sorted_projects = sorted(config_analysis['projects_with_resources'].items(), 
                                   key=lambda x: x[1], reverse=True)
            for project, count in sorted_projects[:5]:
                lines.append(f"      - {project}: {count} resources")
            if len(sorted_projects) > 5:
                remaining = len(sorted_projects) - 5
                lines.append(f"      ... and {remaining} more projects")
        
        return "\n".join(lines)
            
    def setup_working_directory(self, projects: List) -> Tuple[Path, bool]:
        """Setup working directory and check for existing config with enhanced analysis"""
        if self.config.output_directory:
            work_dir = self.config.output_directory
        else:
            work_dir = Path.cwd() / "transifex_downloads"
            
        work_dir.mkdir(parents=True, exist_ok=True)
        
        # Check for existing configuration with enhanced analysis
        config_exists, config_analysis, missing_projects, extra_projects = self.check_existing_config_enhanced(work_dir, projects)
        
        if config_exists:
            tx_config_path = work_dir / ".tx" / "config"
            print(f"\n📁 Found existing Transifex configuration in {work_dir}")
            
            # Fix any quote issues in existing config
            self._fix_config_quotes(tx_config_path)
            
            # Display enhanced status message
            status_message = self.generate_config_status_message(config_analysis, projects, missing_projects)
            print(status_message)
            
            if extra_projects:
                print(f"\n   ⚠️  {len(extra_projects)} projects in config but not in your current selection:")
                for proj in sorted(extra_projects)[:3]:
                    print(f"      - {proj}")
                if len(extra_projects) > 3:
                    print(f"      ... and {len(extra_projects) - 3} more")
            
            # Generate smart action prompts based on analysis
            print("\n🤔 What would you like to do?")
            
            if not missing_projects and config_analysis['config_age_hours'] and config_analysis['config_age_hours'] < 24:
                # Recent config, no new projects
                print("  [1] Use existing configuration as-is (recommended)")
                print("  [2] Check for new resources in existing projects")
                print("  [3] Start fresh (backup existing and create new)")
                print("  [4] Cancel operation")
                
                choice = input("\nChoose [1/2/3/4]: ").strip()
                
                if choice == "1":
                    print("✅ Using existing configuration")
                    return work_dir, True
                elif choice == "2":
                    return work_dir, False
            else:
                # New projects available or old config
                if missing_projects:
                    print(f"  [1] Add {len(missing_projects)} new projects to existing config")
                    print("  [2] Use existing configuration (ignore new projects)")
                    start_fresh_choice = "3"
                    cancel_choice = "4"
                else:
                    print("  [1] Use existing configuration as-is")
                    start_fresh_choice = "2"
                    cancel_choice = "3"
                
                if config_analysis['config_age_hours'] and config_analysis['config_age_hours'] > 72:  # 3+ days old
                    check_choice = start_fresh_choice
                    start_fresh_choice = str(int(start_fresh_choice) + 1)
                    cancel_choice = str(int(cancel_choice) + 1)
                    print(f"  [{check_choice}] Check for new resources in existing projects")
                
                print(f"  [{start_fresh_choice}] Start fresh (backup existing and create new)")
                print(f"  [{cancel_choice}] Cancel operation")
                
                choice = input(f"\nChoose [1/{cancel_choice}]: ").strip()
                
                if choice == "1":
                    if missing_projects:
                        print(f"✅ Will add {len(missing_projects)} new project(s)")
                        # Filter projects list to only missing ones
                        projects[:] = [p for p in projects if p.slug in missing_projects]
                        return work_dir, False
                    else:
                        print("✅ Using existing configuration")
                        return work_dir, True
                elif choice == "2" and missing_projects:
                    print("✅ Using existing configuration (ignoring new projects)")
                    return work_dir, True
                elif choice in [start_fresh_choice, "2"] and not missing_projects:
                    # Start fresh option
                    pass  # Fall through to start fresh logic below
                elif choice == check_choice if 'check_choice' in locals() else "never":
                    return work_dir, False
                elif choice == cancel_choice:
                    print("❌ Operation cancelled")
                    sys.exit(0)
                else:
                    print("❌ Invalid choice, operation cancelled")
                    sys.exit(0)
            
            # Start fresh logic
            backup_path = work_dir / ".tx" / f"config.backup.{int(time.time())}"
            shutil.copy2(tx_config_path, backup_path)
            print(f"📦 Backed up existing config to: {backup_path}")
            
            # Remove existing config
            tx_config_path.unlink()
            
            # Reinitialize
            print("🔧 Initializing fresh Transifex project...")
            result = subprocess.run(['tx', 'init'], cwd=work_dir, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to initialize tx project: {result.stderr}")
            
            return work_dir, False
        else:
            # Initialize new tx project
            print(f"🔧 Initializing new Transifex project in {work_dir}")
            result = subprocess.run(['tx', 'init'], cwd=work_dir, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"Failed to initialize tx project: {result.stderr}")
            
            return work_dir, False
        
    def _add_single_project_with_lock(self, project, work_dir: Path) -> Tuple[str, bool, str, int]:
        """Add a single project using tx add remote command with file locking (Unix)"""
        env = os.environ.copy()
        env['TX_TOKEN'] = self.config.api_token
        
        project_url = f"https://app.transifex.com/{self.config.organization_slug}/{project.slug}"
        
        # Sanitize file filter to handle resource names with single quotes
        # Replace single quotes with underscores to prevent config file issues
        sanitized_file_filter = self.config.file_filter.replace("'", "_")
        
        cmd = [
            'tx', 'add', 'remote',
            '--file-filter', sanitized_file_filter,
            project_url
        ]
        
        if self.config.minimum_perc > 0:
            cmd.extend(['--minimum-perc', str(self.config.minimum_perc)])
        
        # Use a lock file to prevent race conditions
        lock_file_path = work_dir / ".tx" / "config.lock"
        
        try:
            self.logger.info(f"Executing: {' '.join(cmd)}")
            
            # Create lock file and acquire exclusive lock
            lock_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_file_path, 'w') as lock_file:
                try:
                    # Acquire exclusive lock (blocks if another process has it)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                    
                    # Run the command while holding the lock
                    result = subprocess.run(
                        cmd,
                        cwd=work_dir,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=self.config.add_remote_timeout
                    )
                    
                finally:
                    # Release lock (automatically released when file closes)
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            
            success = result.returncode == 0
            resources_added = result.stdout.count('[o:') if success else 0
            
            # Better error reporting for debugging
            if not success:
                error_msg = result.stderr.strip() or result.stdout.strip()
                self.logger.warning(f"Project {project.slug} failed: {error_msg}")
                self.logger.debug(f"Command: {' '.join(cmd)}")
                self.logger.debug(f"Return code: {result.returncode}")
                self.logger.debug(f"Stdout: {result.stdout}")
                self.logger.debug(f"Stderr: {result.stderr}")
            elif resources_added == 0:
                # Project succeeded but added 0 resources - this might be normal for empty projects
                self.logger.info(f"Project {project.slug} succeeded but has no resources")
                self.logger.debug(f"Output: {result.stdout}")
            
            error_msg = result.stderr.strip() or result.stdout.strip() if not success else ""
            
            return project.slug, success, error_msg, resources_added
            
        except subprocess.TimeoutExpired:
            return project.slug, False, f"Timeout after {self.config.add_remote_timeout}s", 0
        except Exception as e:
            return project.slug, False, str(e), 0
        finally:
            # Clean up lock file if it exists
            try:
                lock_file_path.unlink(missing_ok=True)
            except:
                pass
    
    def _add_single_project_windows(self, project, work_dir: Path) -> Tuple[str, bool, str, int]:
        """Windows version without fcntl"""
        env = os.environ.copy()
        env['TX_TOKEN'] = self.config.api_token
        
        project_url = f"https://app.transifex.com/{self.config.organization_slug}/{project.slug}"
        
        # Sanitize file filter to handle resource names with single quotes
        # Replace single quotes with underscores to prevent config file issues
        sanitized_file_filter = self.config.file_filter.replace("'", "_")
        
        cmd = [
            'tx', 'add', 'remote',
            '--file-filter', sanitized_file_filter,
            project_url
        ]
        
        if self.config.minimum_perc > 0:
            cmd.extend(['--minimum-perc', str(self.config.minimum_perc)])
        
        try:
            self.logger.info(f"Executing: {' '.join(cmd)}")
            
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.config.add_remote_timeout
            )
            
            success = result.returncode == 0
            resources_added = result.stdout.count('[o:') if success else 0
            
            if not success:
                error_msg = result.stderr.strip() or result.stdout.strip()
                self.logger.warning(f"Project {project.slug} failed: {error_msg}")
            
            error_msg = result.stderr.strip() or result.stdout.strip() if not success else ""
            
            return project.slug, success, error_msg, resources_added
            
        except subprocess.TimeoutExpired:
            return project.slug, False, f"Timeout after {self.config.add_remote_timeout}s", 0
        except Exception as e:
            return project.slug, False, str(e), 0
    
    def generate_config_for_projects(self, projects: List, work_dir: Path) -> int:
        """Generate .tx/config entries using sequential tx add remote commands"""
        if not projects:
            print("ℹ️  No projects to configure")
            return 0
            
        print("⚙️ Generating configuration for projects...")
        
        start_time = time.time()
        
        # Initialize progress tracking
        added_count = 0
        failed_count = 0
        total_resources = 0
        
        # Choose the appropriate method based on platform
        add_method = self._add_single_project_with_lock if fcntl else self._add_single_project_windows
        
        # Create progress bar
        with tqdm(total=len(projects), 
                  desc="Adding projects", 
                  unit="project",
                  bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}] {postfix}') as pbar:
            
            # Process projects sequentially to avoid config file corruption
            for project in projects:
                try:
                    project_slug, success, error_msg, resources_added = add_method(project, work_dir)
                    
                    if success:
                        added_count += 1
                        total_resources += resources_added
                    else:
                        failed_count += 1
                        self.logger.error(f"Failed to add {project_slug}: {error_msg}")
                    
                    # Update progress bar
                    pbar.update(1)
                    pbar.set_postfix_str(f"✅{added_count} ❌{failed_count}")
                    
                except Exception as e:
                    failed_count += 1
                    pbar.update(1)
                    pbar.set_postfix_str(f"✅{added_count} ❌{failed_count}")
                    self.logger.error(f"Exception processing project {project.slug}: {e}")
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n📊 Configuration complete in {duration:.1f}s:")
        print(f"   ✅ {added_count} projects succeeded ({total_resources} total resources)")
        print(f"   ❌ {failed_count} projects failed")
        print(f"   ⚡ Average: {duration/len(projects):.2f}s per project")
        
        # Fix any quote issues in the generated config
        tx_config_path = work_dir / ".tx" / "config"
        if added_count > 0:
            self._fix_config_quotes(tx_config_path)
        
        # Count actual resources added to config
        if added_count > 0:
            actual_resources = self._count_expected_resources(work_dir)
            if actual_resources > 0:
                print(f"\n📦 {actual_resources} resources added to configuration")
            else:
                print("\n⚠️  WARNING: Projects were added but no resources found!")
                print("   This might indicate:")
                print("   - Projects exist but have no translatable resources")
                print("   - File filter pattern doesn't match any files")
                print("   - Projects are empty or misconfigured")
                print(f"   Check log file for detailed tx add remote output")
        
        return added_count
    
    def _fix_config_quotes(self, config_path: Path) -> bool:
        """
        Fix single quote issues in .tx/config file by replacing them with underscores
        in resource names and file paths.
        
        Returns True if file was modified, False otherwise.
        """
        if not config_path.exists():
            return False
            
        try:
            # Memory-efficient: Process file in chunks for large configs
            max_file_size = 10 * 1024 * 1024  # 10MB limit for in-memory processing
            file_size = config_path.stat().st_size
            
            if file_size > max_file_size:
                # For very large files, use temporary file processing
                return self._fix_config_quotes_streaming(config_path)
            
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            original_content = content
            
            # Fix resource_name lines with unescaped single quotes
            # Pattern: resource_name = Some Name's Resource
            resource_name_pattern = re.compile(r'^(\s*resource_name\s*=\s*)(.*)$', re.MULTILINE)
            
            def fix_resource_name(match):
                prefix = match.group(1)
                name = match.group(2).strip()
                # Replace single quotes with underscores in resource names
                fixed_name = name.replace("'", "_")
                return f"{prefix}{fixed_name}"
            
            content = resource_name_pattern.sub(fix_resource_name, content)
            
            # Fix file_filter and source_file paths that might contain quotes
            file_path_pattern = re.compile(r'^(\s*(?:file_filter|source_file)\s*=\s*)(.*)$', re.MULTILINE)
            
            def fix_file_path(match):
                prefix = match.group(1)
                path = match.group(2).strip()
                # Replace single quotes with underscores in file paths
                fixed_path = path.replace("'", "_")
                return f"{prefix}{fixed_path}"
            
            content = file_path_pattern.sub(fix_file_path, content)
            
            # Write back if changes were made
            if content != original_content:
                # Backup original file
                backup_path = config_path.with_suffix(f'.backup.{int(time.time())}')
                shutil.copy2(config_path, backup_path)
                
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                
                self.logger.info(f"Fixed quote issues in {config_path}, backup saved to {backup_path}")
                print(f"🔧 Fixed quote issues in .tx/config (backup saved)")
                return True
            
            return False
            
        except Exception as e:
            self.logger.warning(f"Could not fix config quotes: {e}")
            return False
    
    def _fix_config_quotes_streaming(self, config_path: Path) -> bool:
        """Streaming version for large config files"""
        try:
            backup_path = config_path.with_suffix(f'.backup.{int(time.time())}')
            temp_path = config_path.with_suffix('.tmp')
            
            # Copy original to backup
            shutil.copy2(config_path, backup_path)
            
            modified = False
            resource_name_pattern = re.compile(r'^(\s*resource_name\s*=\s*)(.*)$')
            file_path_pattern = re.compile(r'^(\s*(?:file_filter|source_file)\s*=\s*)(.*)$')
            
            with open(config_path, 'r', encoding='utf-8') as infile, \
                 open(temp_path, 'w', encoding='utf-8') as outfile:
                
                for line in infile:
                    original_line = line
                    
                    # Fix resource names
                    match = resource_name_pattern.match(line)
                    if match:
                        prefix = match.group(1)
                        name = match.group(2).strip()
                        if "'" in name:
                            fixed_name = name.replace("'", "_")
                            line = f"{prefix}{fixed_name}\n"
                            modified = True
                    
                    # Fix file paths
                    match = file_path_pattern.match(line)
                    if match:
                        prefix = match.group(1)
                        path = match.group(2).strip()
                        if "'" in path:
                            fixed_path = path.replace("'", "_")
                            line = f"{prefix}{fixed_path}\n"
                            modified = True
                    
                    outfile.write(line)
            
            if modified:
                # Replace original with modified version
                shutil.move(temp_path, config_path)
                self.logger.info(f"Fixed quote issues in {config_path} (streaming), backup saved to {backup_path}")
                print(f"🔧 Fixed quote issues in .tx/config (streaming mode, backup saved)")
                return True
            else:
                # Remove temp file if no changes
                temp_path.unlink(missing_ok=True)
                return False
                
        except Exception as e:
            # Cleanup on error
            try:
                temp_path.unlink(missing_ok=True)
            except:
                pass
            self.logger.warning(f"Could not fix config quotes (streaming): {e}")
            return False
    
    def _safe_rglob(self, directory: Path, max_items: int = 100000):
        """Memory-efficient directory traversal with limits"""
        count = 0
        try:
            for item in directory.rglob("*"):
                yield item
                count += 1
                if count >= max_items:
                    break
        except (OSError, PermissionError) as e:
            self.logger.warning(f"Directory traversal error: {e}")
    
    def _format_elapsed_time(self, elapsed_seconds: int) -> str:
        """Format elapsed time progressively as seconds, minutes, or hours"""
        if elapsed_seconds < 60:
            return f"{elapsed_seconds}s"
        elif elapsed_seconds < 3600:  # Less than 1 hour
            minutes = elapsed_seconds // 60
            seconds = elapsed_seconds % 60
            return f"{minutes}m {seconds}s"
        else:  # 1 hour or more
            hours = elapsed_seconds // 3600
            minutes = (elapsed_seconds % 3600) // 60
            seconds = elapsed_seconds % 60
            if seconds > 0:
                return f"{hours}h {minutes}m {seconds}s"
            else:
                return f"{hours}h {minutes}m"
    
    def _count_expected_resources(self, work_dir: Path) -> int:
        """Count expected resources from .tx/config file"""
        config_path = work_dir / ".tx" / "config"
        if not config_path.exists():
            return 0
        
        try:
            config_analysis = self.parse_existing_config_enhanced(config_path)
            return config_analysis['total_resources']
        except:
            return 0
    
    
    def _process_line(self, line: str, phase: str, phase_start_time: float, last_displayed: dict) -> tuple:
        """Process a single line of CLI output and update progress tracking"""
        line_stripped = line.strip()
        
        # Detect phase transitions
        if line_stripped == "# Getting info about resources":
            phase = "info"
            phase_start_time = time.time()
            last_displayed['current'] = 0
            last_displayed['total'] = 0
            print(f"\n🔍 Getting resource info...", end="", flush=True)
            
        elif line_stripped == "# Pulling files":
            # Complete previous phase if needed (only if not already completed)
            if phase == "info" and last_displayed.get('total', 0) > 0 and last_displayed.get('current', 0) < last_displayed.get('total', 0):
                elapsed = int(time.time() - phase_start_time)
                elapsed_str = self._format_elapsed_time(elapsed)
                print(f"\r🔍 Getting resource info ({last_displayed['total']}/{last_displayed['total']}) [{elapsed_str}] ✓")
            
            phase = "downloading"
            phase_start_time = time.time()
            last_displayed['current'] = 0
            last_displayed['total'] = 0
            print(f"📥 Downloading files...", end="", flush=True)
        
        # Extract numbers from CLI output (pattern: (X / Y))
        if "(" in line_stripped and "/" in line_stripped and ")" in line_stripped:
            match = re.search(r'\(\s*(\d+)\s*/\s*(\d+)\s*\)', line_stripped)
            if match:
                current = int(match.group(1))
                total = int(match.group(2))
                
                # Only update display if numbers changed
                if current != last_displayed.get('current') or total != last_displayed.get('total'):
                    last_displayed['current'] = current
                    last_displayed['total'] = total
                    
                    elapsed = int(time.time() - phase_start_time) if phase_start_time else 0
                    elapsed_str = self._format_elapsed_time(elapsed)
                    
                    if phase == "downloading":
                        display = f"\r📥 Downloading files ({current}/{total}) [{elapsed_str}]"
                    else:
                        display = f"\r🔍 Getting resource info ({current}/{total}) [{elapsed_str}]"
                    
                    print(display, end="", flush=True)
                    
                    # Add checkmark if complete
                    if current == total and total > 0:
                        print(" ✓")
        
        # Handle errors - display on new line
        elif line_stripped and any(pattern in line_stripped.lower() for pattern in [
            'error:', 'failed', 'timeout', 'warning:', 'abort', 'exception'
        ]):
            print(f"\n⚠️  {line_stripped}")
        
        # Show summary messages
        elif line_stripped and any(pattern in line_stripped.lower() for pattern in [
            'got info about resources:', 'pulled files:'
        ]):
            print(f"\n✅ {line_stripped}")
        
        return phase, phase_start_time
    
    def _execute_with_progress_bars(self, cmd: List[str], work_dir: Path, env: dict) -> subprocess.CompletedProcess:
        """Execute command with streaming output to avoid memory accumulation"""
        
        print()  # Add a blank line
        
        try:
            # Use PTY to get progress numbers, but keep simple parsing
            try:
                import pty
                import select
                master, slave = pty.openpty()
                use_pty = True
            except (ImportError, OSError):
                # Fallback for Windows
                use_pty = False
            
            if use_pty:
                process = subprocess.Popen(
                    cmd,
                    cwd=work_dir,
                    env=env,
                    stdout=slave,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                os.close(slave)
                master_file = os.fdopen(master, 'r')
            else:
                process = subprocess.Popen(
                    cmd,
                    cwd=work_dir,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                master_file = process.stdout
            
            # Memory-efficient: Don't store all output, just process line by line
            phase = "starting"
            phase_start_time = None
            last_displayed = {}  # Track last displayed values
            error_lines = []  # Only store error lines for final report
            max_error_lines = 20  # Limit error storage
            
            timeout = 7200  # 2 hour timeout
            start_time = time.time()
            
            while True:
                # Check for timeout
                if time.time() - start_time > timeout:
                    process.terminate()
                    process.wait(timeout=5)
                    try:
                        master_file.close()
                    except:
                        pass
                    if use_pty:
                        try:
                            os.close(master)
                        except:
                            pass
                    raise subprocess.TimeoutExpired(cmd, timeout)
                
                # Read line with PTY support
                if use_pty:
                    # Check if process finished
                    if process.poll() is not None:
                        # Read remaining data
                        try:
                            while True:
                                ready, _, _ = select.select([master_file], [], [], 0.1)
                                if ready:
                                    line = master_file.readline()
                                    if line:
                                        # Process line immediately without storing
                                        phase, phase_start_time = self._process_line(
                                            line, phase, phase_start_time, last_displayed
                                        )
                                        # Only store error lines for later analysis
                                        if any(pattern in line.lower() for pattern in ['error:', 'failed', 'exception']):
                                            if len(error_lines) < max_error_lines:
                                                error_lines.append(line.strip())
                                    else:
                                        break
                                else:
                                    break
                        except:
                            pass
                        break
                    
                    # Regular read with timeout
                    try:
                        ready, _, _ = select.select([master_file], [], [], 0.1)
                        if ready:
                            line = master_file.readline()
                            if line:
                                # Process line immediately without storing
                                phase, phase_start_time = self._process_line(
                                    line, phase, phase_start_time, last_displayed
                                )
                                # Only store error lines for later analysis
                                if any(pattern in line.lower() for pattern in ['error:', 'failed', 'exception']):
                                    if len(error_lines) < max_error_lines:
                                        error_lines.append(line.strip())
                    except:
                        break
                else:
                    # Regular subprocess reading
                    line = master_file.readline()
                    
                    # Check if process finished
                    if not line and process.poll() is not None:
                        break
                    
                    if line:
                        # Process line immediately without storing
                        phase, phase_start_time = self._process_line(
                            line, phase, phase_start_time, last_displayed
                        )
                        # Only store error lines for later analysis
                        if any(pattern in line.lower() for pattern in ['error:', 'failed', 'exception']):
                            if len(error_lines) < max_error_lines:
                                error_lines.append(line.strip())
            
            # Clean up
            try:
                master_file.close()
            except:
                pass
            
            if use_pty:
                try:
                    os.close(master)
                except:
                    pass
            
            # Final summary
            print()  # Ensure we're on a new line
            total_elapsed = int(time.time() - start_time)
            total_time_str = self._format_elapsed_time(total_elapsed)
            
            print(f"\n✅ Completed in {total_time_str}")
            
            return_code = process.poll()
            # Memory-efficient: Create minimal output summary instead of storing everything
            error_summary = '\n'.join(error_lines) if error_lines else ''
            
            # Create result object with only essential data
            result = type('MockResult', (), {
                'returncode': return_code,
                'stdout': f"Process completed with {len(error_lines)} errors" if error_lines else "Process completed successfully",
                'stderr': error_summary
            })()
            
            return result
            
        except subprocess.TimeoutExpired:
            raise
        except Exception as e:
            # Handle other exceptions
            result = type('MockResult', (), {
                'returncode': 1,
                'stdout': '',
                'stderr': str(e)
            })()
            return result
    
        
    def execute_bulk_download(self, work_dir: Path) -> Tuple[bool, str]:
        """Execute bulk download using official CLI"""
        print("\n🚀 Starting Download...")
        
        # Build pull command
        cmd = ['tx', 'pull']
        
        # Add download mode flags
        if self.config.download_mode == "source":
            cmd.append('--source')
        elif self.config.download_mode == "translations":
            cmd.append('--translations')
        elif self.config.download_mode == "both":
            cmd.extend(['--source', '--translations'])
            
        # Add language filters
        if self.config.language_codes:
            cmd.extend(['--languages', ','.join(self.config.language_codes)])
        else:
            cmd.append('--all')
            
        # Add performance and reliability flags
        cmd.extend(['--workers', str(self.config.workers)])
        
        if self.config.skip_on_error:
            cmd.append('--skip')
            
        if self.config.force_download:
            cmd.append('--force')
        elif self.config.skip_existing_files:
            cmd.append('--disable-overwrite')
            
        # Handle output modes - custom progress bars need verbose output
        if self.config.use_filtered_output:
            # Don't add --silent, we need verbose output to parse progress
            pass
        elif self.config.use_silent_mode:
            cmd.append('--silent')
            
        # Set up environment
        env = os.environ.copy()
        env['TX_TOKEN'] = self.config.api_token
        
        print(f"🔧 Command: {' '.join(cmd)}")
        print(f"📁 Working directory: {work_dir}")
        print(f"👥 Workers: {self.config.workers}")
        print(f"🌐 Languages: {'all' if not self.config.language_codes else ', '.join(self.config.language_codes)}")
        print(f"📥 Mode: {self.config.download_mode}")
        print()
        
        try:
            start_time = time.time()
            
            # Execute the download
            if self.config.use_filtered_output:
                # Custom progress bars mode - parse silent output with tqdm progress
                result = self._execute_with_progress_bars(cmd, work_dir, env)
            elif self.config.use_silent_mode:
                # Silent mode - capture output
                result = subprocess.run(
                    cmd,
                    cwd=work_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=7200  # 2 hour timeout
                )
            else:
                # Interactive mode - show output
                result = subprocess.run(
                    cmd,
                    cwd=work_dir,
                    env=env,
                    text=True,
                    timeout=7200  # 2 hour timeout
                )
            
            end_time = time.time()
            duration = end_time - start_time
            
            if result.returncode == 0:
                return True, f"Download completed successfully in {duration:.1f}s"
            else:
                error_detail = ""
                if self.config.use_silent_mode and result.stderr:
                    error_detail = f"\nError: {result.stderr[:500]}"
                elif self.config.use_filtered_output and hasattr(result, 'stderr') and result.stderr:
                    error_detail = f"\nError: {result.stderr[:500]}"
                return False, f"Download failed with exit code {result.returncode}{error_detail}"
                
        except subprocess.TimeoutExpired:
            return False, "Download timed out after 2 hours"
        except KeyboardInterrupt:
            return False, "Download interrupted by user"
        except Exception as e:
            return False, f"Download failed with error: {e}"
            
    def generate_download_report(self, work_dir: Path, success: bool, message: str) -> None:
        """Generate a comprehensive download report"""
        print("\n" + "="*60)
        print("📊 DOWNLOAD REPORT")
        print("="*60)
        
        # Memory-efficient file counting with generator and limits
        total_files = 0
        file_types = {}
        total_size = 0
        max_files_to_process = 100000  # Limit to prevent memory issues with huge directories
        
        if work_dir.exists():
            file_count = 0
            for file_path in self._safe_rglob(work_dir, max_files_to_process):
                if file_path.is_file() and not file_path.name.startswith('.'):
                    total_files += 1
                    ext = file_path.suffix.lower()
                    file_types[ext] = file_types.get(ext, 0) + 1
                    try:
                        total_size += file_path.stat().st_size
                    except:
                        pass
                    
                file_count += 1
                if file_count >= max_files_to_process:
                    print(f"⚠️  Directory scan limited to {max_files_to_process} items for memory efficiency")
                    break
        
        print(f"📁 Working directory: {work_dir}")
        print(f"📄 Total files downloaded: {total_files}")
        
        if total_size > 0:
            # Format size
            if total_size < 1024:
                size_str = f"{total_size} B"
            elif total_size < 1024 * 1024:
                size_str = f"{total_size / 1024:.1f} KB"
            elif total_size < 1024 * 1024 * 1024:
                size_str = f"{total_size / (1024 * 1024):.1f} MB"
            else:
                size_str = f"{total_size / (1024 * 1024 * 1024):.1f} GB"
            print(f"💾 Total size: {size_str}")
        
        if file_types:
            print("📋 File types:")
            for ext, count in sorted(file_types.items()):
                print(f"   {ext or '(no extension)'}: {count}")
        
        print(f"\n🎯 Status: {'✅ SUCCESS' if success else '❌ FAILED'}")
        print(f"📝 Details: {message}")
        
        # Show directory structure sample with memory-efficient iteration
        if total_files > 0:
            print("\n📂 Directory structure (sample):")
            count = 0
            sample_files = []
            for file_path in self._safe_rglob(work_dir, 50):  # Only scan first 50 for sample
                if file_path.is_file() and not file_path.name.startswith('.') and count < 10:
                    rel_path = file_path.relative_to(work_dir)
                    sample_files.append(str(rel_path))
                    count += 1
                if count >= 10:
                    break
                    
            # Sort only the sample for display
            for file_rel_path in sorted(sample_files):
                print(f"   {file_rel_path}")
                
            if total_files > 10:
                print(f"   ... and {total_files - 10} more files")
        
        # Save report to file
        report_path = work_dir / f"download_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(f"Transifex Download Report\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Status: {'SUCCESS' if success else 'FAILED'}\n")
                f.write(f"Message: {message}\n")
                f.write(f"Total files: {total_files}\n")
                f.write(f"Working directory: {work_dir}\n")
            print(f"\n📄 Report saved to: {report_path}")
        except:
            pass
        
        print("="*60)
    
    def cleanup_resources(self) -> None:
        """Cleanup resources to prevent memory leaks"""
        try:
            # Close log handler
            if hasattr(self, '_log_handler') and self._log_handler:
                self._log_handler.close()
                if hasattr(self, 'logger') and self.logger:
                    self.logger.removeHandler(self._log_handler)
        except Exception as e:
            print(f"Warning: Error during cleanup: {e}")
        
    def run(self) -> None:
        """Main execution method with proper resource cleanup"""
        try:
            print("🚀 Transifex Bulk Downloader")
            
            # Phase 1: Discovery
            self.validate_token_and_org()
            projects = self.discover_projects()
            
            if not projects:
                print("❌ No projects found to process")
                return
                
            # Phase 2: Setup and configuration
            work_dir, skip_config = self.setup_working_directory(projects)
            
            if not skip_config:
                added_count = self.generate_config_for_projects(projects, work_dir)
                
                if added_count == 0 and not skip_config:
                    print("❌ No projects were successfully configured")
                    return
                    
            # Phase 3: Bulk download
            success, message = self.execute_bulk_download(work_dir)
            
            # Phase 4: Reporting
            self.generate_download_report(work_dir, success, message)
            
        except KeyboardInterrupt:
            print("\n🛑 Operation cancelled by user")
        except Exception as e:
            print(f"\n❌ Fatal error: {e}")
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"Fatal error: {e}", exc_info=True)
        finally:
            # Always cleanup resources
            self.cleanup_resources()

def get_api_token() -> str:
    """Get API token from environment or prompt user"""
    token = os.getenv("TX_TOKEN") or os.getenv("TRANSIFEX_API_TOKEN")
    if token:
        print("✅ Using API token from environment variable")
        return token
    return getpass.getpass("🔑 Enter your Transifex API token: ")

def get_user_config() -> DiscoveryConfig:
    """Get configuration from user input or file"""
    config_path = Path("hybrid_config.json")
    
    if config_path.exists():
        print(f"📄 Found configuration file: {config_path}")
        if input("Load configuration from file? [Y/n]: ").strip().lower() not in ['n', 'no']:
            try:
                config = DiscoveryConfig.load_from_file(config_path)
                if not config.api_token or config.api_token.startswith('***'):
                    config.api_token = get_api_token()
                print(f"✅ Configuration loaded from {config_path}")
                return config
            except Exception as e:
                print(f"⚠️  Could not load config file: {e}")
                print("Proceeding with interactive setup...")
    
    print("\n🔧 Interactive Configuration Setup")
    
    # Get basic settings
    api_token = get_api_token()
    organization_slug = input("🏢 Enter your Organization slug: ").strip()
    
    # Download mode
    print("\n📥 What to download?")
    print("  [1] Source files only")
    print("  [2] Translation files only") 
    print("  [3] Both source and translations")
    mode_choice = input("Choose [1/2/3]: ").strip()
    mode_map = {"1": "source", "2": "translations", "3": "both"}
    download_mode = mode_map.get(mode_choice, "both")
    
    # Project filtering
    project_slugs = None
    if input("\n📋 Download from [a]ll projects or [s]pecific ones? [a/s]: ").strip().lower() == "s":
        project_input = input("Enter project slugs (comma-separated): ").strip()
        if project_input:
            project_slugs = [slug.strip() for slug in project_input.split(",")]
    
    # Language filtering
    language_codes = None
    if download_mode in ["translations", "both"]:
        if input("\n🌐 Download [a]ll languages or [s]pecific ones? [a/s]: ").strip().lower() == "s":
            lang_input = input("Enter language codes (e.g. en,es,fr): ").strip()
            if lang_input:
                language_codes = [code.strip() for code in lang_input.split(",")]
    
    # Output directory
    output_dir_input = input("\n📁 Output directory (press Enter for './transifex_downloads'): ").strip()
    output_directory = Path(output_dir_input) if output_dir_input else None
    
    # Advanced settings
    workers = 8
    if input("\n⚙️  Configure advanced settings? [y/N]: ").strip().lower() in ['y', 'yes']:
        try:
            workers = int(input(f"👥 Number of workers [1-30, default {workers}]: ") or workers)
            workers = max(1, min(workers, 30))
        except ValueError:
            pass
    
    config = DiscoveryConfig(
        api_token=api_token,
        organization_slug=organization_slug,
        project_slugs=project_slugs,
        download_mode=download_mode,
        language_codes=language_codes,
        output_directory=output_directory,
        workers=workers
    )
    
    # Save configuration
    if input(f"\n💾 Save configuration to {config_path}? [Y/n]: ").strip().lower() not in ['n', 'no']:
        try:
            config.save_to_file(config_path)
            print(f"✅ Configuration saved (token omitted for security)")
        except Exception as e:
            print(f"⚠️  Could not save configuration: {e}")
    
    return config

def main():
    """Main entry point"""
    try:
        config = get_user_config()
        downloader = HybridTransifexDownloader(config)
        downloader.run()
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")

if __name__ == "__main__":
    main()