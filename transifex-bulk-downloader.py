#!/usr/bin/env python3
"""
Transifex Bulk Downloader
Combines Python API discovery with official CLI downloads

Features:
- 🔍 Discovers projects in organization using Python API
- ⚙️ Generates .tx/config using official 'tx add remote' commands
- 🚀 Downloads using official 'tx pull' 
- 📊 Downloads TMX (Translation Memory) files
- 🎯 Simple configuration with mode selection

Version: 2.0
"""

import subprocess
import sys
import os
import json
import getpass
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass

# Try to import required packages
try:
    from transifex.api import transifex_api
    import requests
except ImportError as e:
    missing_pkg = str(e).split("'")[1] if "'" in str(e) else str(e)
    print(f"❌ Missing required package: {missing_pkg}")
    print("📦 Please install with: pip install transifex-python requests")
    sys.exit(1)

# Constants
DEFAULT_WORKERS = 12
MAX_WORKERS = 30
TRANSLATION_MODES = [
    'default', 'reviewed', 'proofread', 'translator', 'untranslated', 
    'onlytranslated', 'onlyreviewed', 'onlyproofread', 'sourceastranslation'
]

@dataclass
class Config:
    """Simple configuration for bulk operations"""
    api_token: str
    organization_slug: str
    project_slugs: Optional[List[str]] = None
    output_directory: Optional[Path] = None
    file_filter: str = "files/<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>"
    
    @classmethod
    def load_from_file(cls, config_path: Path) -> 'Config':
        """Load configuration from JSON file"""
        with open(config_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return cls(
            api_token=data.get('api_token', ''),
            organization_slug=data.get('organization_slug', ''),
            project_slugs=data.get('project_slugs'),
            output_directory=Path(data['output_directory']) if data.get('output_directory') else None,
            file_filter=data.get('file_filter', 'files/<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>')
        )
    
    def save_to_file(self, config_path: Path) -> None:
        """Save configuration to JSON file"""
        data = {
            'api_token': self.api_token if self.api_token else '*** SET YOUR TOKEN HERE ***',
            'organization_slug': self.organization_slug,
            'project_slugs': self.project_slugs,
            'output_directory': str(self.output_directory) if self.output_directory else None,
            'file_filter': self.file_filter,
            '_modes': TRANSLATION_MODES
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

class BulkDownloader:
    """Bulk downloader"""
    
    def __init__(self, config: Config):
        self.config = config
        self.organization = None
        self._setup_api()
        self._verify_cli()
    
    def _setup_api(self) -> None:
        """Initialize Transifex API"""
        transifex_api.setup(auth=self.config.api_token)
    
    def _verify_cli(self) -> None:
        """Verify Transifex CLI is available"""
        try:
            result = subprocess.run(['tx', '--version'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                print(f"✅ Transifex CLI: {result.stdout.strip()}")
            else:
                raise FileNotFoundError()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print("❌ Transifex CLI not found")
            print("📋 Install from: https://github.com/transifex/cli/releases")
            sys.exit(1)
    
    def validate_token_and_org(self) -> None:
        """Validate API token and organization"""
        print("🔑 Validating API token and organization...")
        
        try:
            self.organization = transifex_api.Organization.get(slug=self.config.organization_slug)
            print(f"✅ Organization: {self.organization.name}")
            
            # Test project access
            projects_iter = self.organization.fetch("projects").all()
            next(projects_iter, None)  # Just test we can access
            print("✅ API access verified")
            
        except Exception as e:
            error_msg = str(e).lower()
            if "unauthorized" in error_msg or "403" in error_msg:
                raise ValueError("API token invalid or insufficient permissions")
            elif "not found" in error_msg or "404" in error_msg:
                raise ValueError(f"Organization '{self.config.organization_slug}' not found")
            else:
                raise ValueError(f"API validation failed: {e}")
    
    def discover_projects(self) -> List:
        """Discover projects in organization"""
        print("🔍 Discovering projects...")
        
        projects_iterator = self.organization.fetch("projects").all()
        
        if self.config.project_slugs:
            # Filter to specific projects
            target_slugs = set(self.config.project_slugs)
            filtered_projects = []
            found_slugs = set()
            
            for project in projects_iterator:
                if project.slug in target_slugs:
                    filtered_projects.append(project)
                    found_slugs.add(project.slug)
                    if len(found_slugs) == len(target_slugs):
                        break
            
            missing_slugs = target_slugs - found_slugs
            if missing_slugs:
                print(f"⚠️  Projects not found: {', '.join(missing_slugs)}")
            
            print(f"📋 Found {len(filtered_projects)} of {len(self.config.project_slugs)} requested projects")
            return filtered_projects
        else:
            # Get all projects
            all_projects = list(projects_iterator)
            print(f"📋 Found {len(all_projects)} projects in organization")
            return all_projects
    
    def setup_working_directory(self, projects: List) -> tuple[Path, bool]:
        """Setup working directory and return (work_dir, skip_config_generation)"""
        if self.config.output_directory:
            base_dir = self.config.output_directory
        else:
            base_dir = Path.cwd() / "transifex_downloads"
        
        base_dir.mkdir(parents=True, exist_ok=True)
        
        # Create files subdirectory for actual downloads
        files_dir = base_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        
        # Check for existing config in base directory
        tx_config_path = base_dir / ".tx" / "config"
        if tx_config_path.exists():
            print(f"📁 Found existing .tx/config in {base_dir}")
            choice = input("Use existing config? [Y/n]: ").strip().lower()
            if choice not in ['n', 'no']:
                # Create local .transifexrc to ensure CLI uses correct token
                self._create_local_transifexrc(base_dir)
                return base_dir, True  # Skip config generation, return base_dir for tx commands
            
            # Remove existing and reinitialize
            tx_config_path.unlink()
        
        # Initialize new tx project in base directory
        print(f"🔧 Initializing Transifex project in {base_dir}")
        result = subprocess.run(['tx', 'init'], cwd=base_dir, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to initialize tx project: {result.stderr}")
        
        # Create local .transifexrc to ensure CLI uses correct token
        self._create_local_transifexrc(base_dir)
        
        return base_dir, False  # Don't skip config generation, return base_dir for tx commands
    
    def _create_local_transifexrc(self, work_dir: Path) -> None:
        """Create a local .transifexrc file with the current API token"""
        try:
            transifexrc_path = work_dir / ".transifexrc"
            
            # Create the config content
            config_content = f"""[https://api.transifex.com]
api_hostname = https://api.transifex.com
username = api
password = {self.config.api_token}
"""
            
            # Write the file
            with open(transifexrc_path, 'w', encoding='utf-8') as f:
                f.write(config_content)
            
            print(f"📝 Created local .transifexrc in {work_dir}")
            
        except Exception as e:
            print(f"⚠️  Warning: Could not create .transifexrc: {e}")
            # Don't fail the operation if we can't create the file
    
    def generate_config_for_projects(self, projects: List, work_dir: Path) -> None:
        """Generate .tx/config entries using tx add remote"""
        if not projects:
            print("ℹ️  No projects to configure")
            return
        
        print("⚙️ Adding projects to configuration...")
        
        env = os.environ.copy()
        env['TX_TOKEN'] = self.config.api_token
        
        added_count = 0
        failed_count = 0
        
        for i, project in enumerate(projects, 1):
            # Clear the line and print the full project slug
            print(f"\rAdding projects ({i}/{len(projects)}): {project.slug}" + " " * 20, end="", flush=True)
            
            project_url = f"https://app.transifex.com/{self.config.organization_slug}/{project.slug}"
            
            cmd = [
                'tx', 'add', 'remote',
                '--file-filter', self.config.file_filter,
                project_url
            ]
            
            result = subprocess.run(cmd, cwd=work_dir, env=env, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"\r⚠️  Failed to add {project.slug}: {result.stderr.strip()}")
                failed_count += 1
            else:
                resources_added = result.stdout.count('[o:')
                added_count += 1
        
        print(f"\r📦 Configuration complete: ✅{added_count} ❌{failed_count}" + " " * 30)
        print()
    
    def _count_resources_in_config(self, work_dir: Path) -> int:
        """Count resources in existing .tx/config file"""
        config_path = work_dir / ".tx" / "config"
        if not config_path.exists():
            return 0
        
        try:
            resource_count = 0
            with open(config_path, 'r', encoding='utf-8') as f:
                for line in f:
                    # Count lines that start with [o:org:p:project:r:resource]
                    if line.strip().startswith('[o:') and ':p:' in line and ':r:' in line:
                        resource_count += 1
            return resource_count
        except Exception:
            return 0
    
    def _count_downloaded_files(self, work_dir: Path) -> int:
        """Count actual downloaded files (check both files dir and direct)"""
        try:
            file_count = 0
            
            # First check the files subdirectory (preferred location)
            files_dir = work_dir / "files"
            if files_dir.exists():
                for file_path in files_dir.rglob("*"):
                    if file_path.is_file() and not file_path.name.startswith('.'):
                        file_count += 1
            
            # If no files in 'files' dir, check direct in work_dir (fallback)
            if file_count == 0:
                for file_path in work_dir.rglob("*"):
                    if (file_path.is_file() and 
                        not file_path.name.startswith('.') and 
                        not file_path.name == 'config' and
                        '.tx' not in str(file_path)):
                        file_count += 1
            
            return file_count
        except Exception:
            return 0
    
    def execute_file_download(self, work_dir: Path) -> bool:
        """Execute file download using tx pull"""
        print("\n🚀 Starting file download...")
        
        # Ask for download mode
        print("\n📥 Download mode:")
        print("  [1] Source files only")
        print("  [2] Translation files only")
        print("  [3] Both source and translations")
        mode_choice = input("Choose [1/2/3]: ").strip()
        mode_map = {"1": "source", "2": "translations", "3": "both"}
        download_mode = mode_map.get(mode_choice, "both")
        
        # Ask for translation mode if downloading translations
        translation_mode = "default"
        if download_mode in ["translations", "both"]:
            print("\n🎯 Translation mode:")
            for i, mode in enumerate(TRANSLATION_MODES, 1):
                print(f"  [{i}] {mode}")
            
            mode_choice = input(f"Choose [1-{len(TRANSLATION_MODES)}]: ").strip()
            try:
                mode_idx = int(mode_choice) - 1
                if 0 <= mode_idx < len(TRANSLATION_MODES):
                    translation_mode = TRANSLATION_MODES[mode_idx]
            except ValueError:
                pass
        
        # Ask for language selection
        language_codes = None
        if download_mode in ["translations", "both"]:
            print("\n🌐 Languages:")
            print("  [1] All languages")
            print("  [2] Specific languages")
            lang_choice = input("Choose [1/2]: ").strip()
            
            if lang_choice == "2":
                lang_input = input("Enter language codes (e.g. en,es,fr): ").strip()
                if lang_input:
                    language_codes = [code.strip() for code in lang_input.split(",")]
        
        # Ask for number of workers
        print("\n👥 Workers:")
        workers_input = input(f"Number of workers [1-{MAX_WORKERS}, default {DEFAULT_WORKERS}]: ").strip()
        try:
            workers = int(workers_input) if workers_input else DEFAULT_WORKERS
            workers = max(1, min(workers, MAX_WORKERS))
        except ValueError:
            workers = DEFAULT_WORKERS
        
        # Build command
        cmd = ['tx', 'pull']
        
        # Download mode
        if download_mode == "source":
            cmd.append('--source')
        elif download_mode == "translations":
            cmd.append('--translations')
        elif download_mode == "both":
            cmd.extend(['--source', '--translations'])
        
        # Languages (only for translations)
        if download_mode in ["translations", "both"]:
            if language_codes:
                cmd.extend(['--languages', ','.join(language_codes)])
            else:
                cmd.append('--all')
        
        # Translation mode (only for translations)
        if download_mode in ["translations", "both"] and translation_mode != "default":
            cmd.extend(['--mode', translation_mode])
        
        # Workers
        cmd.extend(['--workers', str(workers)])
        
        # Environment
        env = os.environ.copy()
        env['TX_TOKEN'] = self.config.api_token
        
        print(f"🔧 Command: {' '.join(cmd)}")
        print(f"📁 Working directory: {work_dir}")
        
        # Execute download
        result = subprocess.run(cmd, cwd=work_dir, env=env, text=True)
        
        # Check if files were actually downloaded despite error code
        downloaded_files = self._count_downloaded_files(work_dir)
        
        if result.returncode == 0:
            print(f"✅ File download completed successfully ({downloaded_files} files)")
            return True
        else:
            if downloaded_files > 0:
                print(f"⚠️  Download completed with warnings (exit code {result.returncode})")
                print(f"📁 {downloaded_files} files were downloaded successfully")
                # Count partial vs total to show completion rate
                total_resources = self._count_resources_in_config(work_dir)
                if total_resources > 0:
                    completion_rate = (downloaded_files / total_resources) * 100
                    print(f"📊 Completion rate: {completion_rate:.1f}% ({downloaded_files}/{total_resources})")
                return True  # Consider it successful if files were downloaded
            else:
                print(f"❌ File download failed with exit code {result.returncode}")
                print("💡 Try reducing workers or checking network connectivity")
                return False
    
    def execute_tmx_download(self, work_dir: Path) -> bool:
        """Execute TMX download using Python SDK"""
        print("\n🚀 Starting TMX download...")
        
        # Ask for project selection
        print("\n📋 Which projects?")
        print("  [1] All projects in organization")
        print("  [2] Specific projects")
        project_choice = input("Choose [1/2]: ").strip()
        
        if project_choice == "2":
            project_input = input("Enter project slugs (comma-separated): ").strip()
            if project_input:
                project_slugs = [slug.strip() for slug in project_input.split(",")]
            else:
                print("❌ No projects specified")
                return False
        else:
            # Get all projects
            projects = self.discover_projects()
            project_slugs = [p.slug for p in projects]
        
        # Ask for language selection
        print("\n🌐 Language options:")
        print("  [1] One file per project (all languages combined)")
        print("  [2] Separate files per language (all languages)")
        print("  [3] Separate files for specific languages")
        language_choice = input("Choose [1/2/3]: ").strip()
        
        specific_languages = None
        if language_choice == "3":
            lang_input = input("Enter language codes (e.g. en,es,fr): ").strip()
            if lang_input:
                specific_languages = [code.strip() for code in lang_input.split(",")]
        
        tmx_dir = work_dir / "TMX files"
        tmx_dir.mkdir(exist_ok=True)
        
        success_count = 0
        failed_count = 0
        total_files = 0
        
        # Calculate total files for progress
        if language_choice == "1":
            total_files = len(project_slugs)
        else:
            # We'll need to count languages per project, so we'll update progress dynamically
            total_files = len(project_slugs)  # Initial estimate
        
        file_counter = 0
        
        for project_slug in project_slugs:
            try:
                # Get project object
                project = self.organization.fetch("projects").get(slug=project_slug)
                
                if language_choice == "1":
                    # One file for all languages
                    file_counter += 1
                    print(f"\rDownloading TMX ({file_counter}/{total_files}): {project_slug}_all_languages.tmx" + " " * 10, end="", flush=True)
                    
                    url = transifex_api.TmxAsyncDownload.download(project=project)
                    response = requests.get(url)
                    
                    if response.status_code == 200:
                        tmx_file = tmx_dir / f"{project_slug}_all_languages.tmx"
                        with open(tmx_file, 'wb') as f:
                            f.write(response.content)
                        success_count += 1
                    else:
                        failed_count += 1
                
                else:
                    # Separate files per language
                    if language_choice == "2":
                        # All languages
                        languages = list(project.fetch("languages").all())
                    else:
                        # Specific languages
                        all_languages = list(project.fetch("languages").all())
                        languages = [lang for lang in all_languages if lang.code in specific_languages]
                    
                    # Update total files count for better progress tracking
                    if project_slug == project_slugs[0]:  # First project
                        total_files = len(project_slugs) * len(languages)
                    
                    for language in languages:
                        file_counter += 1
                        print(f"\rDownloading TMX ({file_counter}/{total_files}): {project_slug}_{language.code}.tmx" + " " * 10, end="", flush=True)
                        
                        url = transifex_api.TmxAsyncDownload.download(project=project, language=language)
                        response = requests.get(url)
                        
                        if response.status_code == 200:
                            tmx_file = tmx_dir / f"{project_slug}_{language.code}.tmx"
                            with open(tmx_file, 'wb') as f:
                                f.write(response.content)
                            success_count += 1
                        else:
                            failed_count += 1
                
            except Exception as e:
                failed_count += 1
                if failed_count <= 3:  # Show first few errors
                    print(f"\r⚠️  Error with {project_slug}: {e}")
        
        print(f"\r📊 TMX download complete: ✅{success_count} ❌{failed_count}" + " " * 30)
        print()
        return success_count > 0

def get_api_token() -> str:
    """Get API token from environment or user input"""
    token = os.getenv("TX_TOKEN") or os.getenv("TRANSIFEX_API_TOKEN")
    if token:
        print("✅ Using API token from environment")
        return token
    return getpass.getpass("🔑 Enter your Transifex API token: ")

def get_user_config() -> Config:
    """Get configuration from user input or file"""
    config_path = Path("bulk_download_config.json")
    
    if config_path.exists():
        print(f"📄 Found configuration file: {config_path}")
        if input("Load configuration? [Y/n]: ").strip().lower() not in ['n', 'no']:
            try:
                config = Config.load_from_file(config_path)
                if not config.api_token or config.api_token.startswith('***'):
                    config.api_token = get_api_token()
                print(f"✅ Configuration loaded")
                return config
            except Exception as e:
                print(f"⚠️  Could not load config: {e}")
    
    print("\n🔧 Configuration Setup")
    
    # Basic settings
    api_token = get_api_token()
    organization_slug = input("🏢 Organization slug: ").strip()
    
    # Download mode
    print("\n📥 Download mode:")
    print("  [1] Source files only")
    print("  [2] Translation files only")
    print("  [3] Both source and translations")
    mode_choice = input("Choose [1/2/3]: ").strip()
    mode_map = {"1": "source", "2": "translations", "3": "both"}
    download_mode = mode_map.get(mode_choice, "both")
    
    # Translation mode
    translation_mode = "default"
    if download_mode in ["translations", "both"]:
        print("\n🎯 Translation mode:")
        modes = ['default', 'reviewed', 'proofread', 'translator', 'untranslated', 
                'onlytranslated', 'onlyreviewed', 'onlyproofread', 'sourceastranslation']
        for i, mode in enumerate(modes, 1):
            print(f"  [{i}] {mode}")
        
        mode_choice = input(f"Choose [1-{len(modes)}]: ").strip()
        try:
            mode_idx = int(mode_choice) - 1
            if 0 <= mode_idx < len(modes):
                translation_mode = modes[mode_idx]
        except ValueError:
            pass
    
    # Project filtering
    project_slugs = None
    if input("\n📋 Download [a]ll projects or [s]pecific ones? [a/s]: ").strip().lower() == "s":
        project_input = input("Project slugs (comma-separated): ").strip()
        if project_input:
            project_slugs = [slug.strip() for slug in project_input.split(",")]
    
    # Language filtering
    language_codes = None
    if download_mode in ["translations", "both"]:
        if input("\n🌐 Download [a]ll languages or [s]pecific ones? [a/s]: ").strip().lower() == "s":
            lang_input = input("Language codes (e.g. en,es,fr): ").strip()
            if lang_input:
                language_codes = [code.strip() for code in lang_input.split(",")]
    
    # Output directory
    output_dir_input = input("\n📁 Output directory (Enter for './transifex_downloads'): ").strip()
    output_directory = Path(output_dir_input) if output_dir_input else None
    
    config = Config(
        api_token=api_token,
        organization_slug=organization_slug,
        project_slugs=project_slugs,
        download_mode=download_mode,
        translation_mode=translation_mode,
        language_codes=language_codes,
        output_directory=output_directory
    )
    
    # Save configuration
    if input(f"\n💾 Save configuration? [Y/n]: ").strip().lower() not in ['n', 'no']:
        try:
            config.save_to_file(config_path)
            print("✅ Configuration saved")
        except Exception as e:
            print(f"⚠️  Could not save configuration: {e}")
    
    return config

def main():
    """Main entry point"""
    try:
        print("🚀 Transifex Bulk Downloader")
        
        # Choose operation type
        print("\n📋 What would you like to download?")
        print("  [1] Translation files")
        print("  [2] Translation Memory (TMX)")
        
        operation = input("Choose [1/2]: ").strip()
        
        config = get_user_config()
        downloader = BulkDownloader(config)
        downloader.validate_token_and_org()
        
        if operation == "2":
            # TMX download
            base_dir = config.output_directory or Path.cwd() / "transifex_downloads"
            base_dir.mkdir(parents=True, exist_ok=True)
            success = downloader.execute_tmx_download(base_dir)
        else:
            # File download - ask for project selection
            print("\n📋 Which projects?")
            print("  [1] All projects in organization")
            print("  [2] Specific projects")
            project_choice = input("Choose [1/2]: ").strip()
            
            if project_choice == "2":
                project_input = input("Enter project slugs (comma-separated): ").strip()
                if project_input:
                    config.project_slugs = [slug.strip() for slug in project_input.split(",")]
                else:
                    print("❌ No projects specified")
                    return
            else:
                config.project_slugs = None  # All projects
            
            projects = downloader.discover_projects()
            if not projects:
                print("❌ No projects found")
                return
            
            work_dir, skip_config = downloader.setup_working_directory(projects)
            
            if not skip_config:
                downloader.generate_config_for_projects(projects, work_dir)
            else:
                # Count resources in existing config
                resource_count = downloader._count_resources_in_config(work_dir)
                print(f"✅ Using existing configuration ({resource_count} resources)")
            
            success = downloader.execute_file_download(work_dir)
        
        if success:
            print("\n✅ Operation completed successfully")
        else:
            print("\n❌ Operation failed")
    
    except KeyboardInterrupt:
        print("\n🛑 Operation cancelled")
    except Exception as e:
        print(f"\n❌ Error: {e}")

if __name__ == "__main__":
    main()