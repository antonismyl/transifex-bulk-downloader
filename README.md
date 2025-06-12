# Transifex Bulk Downloader

A hybrid Python tool that combines the Transifex API for project discovery with the official Transifex CLI for optimized bulk downloads of source and translation files. This tool is designed to handle large-scale translation downloads from Transifex organizations efficiently.

## Features

- **Automatic Project Discovery**: Discovers all projects in your Transifex organization using the Python API
- **Smart Configuration Management**: Generates and manages `.tx/config` files
- **Optimized Downloads**: Uses the official Transifex CLI with worker pools for fast, parallel downloads
- **Robust Memory Management**: Handles massive organizations and long running downloads without memory leaks

## Requirements

### System Requirements
- Python 3.6 or higher
- Transifex CLI (official command-line tool)

### Python Dependencies
- transifex-python
- requests
- tqdm

## Installation

### 1. Install Python Dependencies
```bash
pip install transifex-python requests tqdm
```

### 2. Install Transifex CLI
The official Transifex CLI is required for bulk downloads.

**Linux/macOS:**
```bash
curl -o- https://raw.githubusercontent.com/transifex/cli/master/install.sh | bash
```

**Windows:**
Download the latest release from the [Transifex CLI releases page](https://github.com/transifex/cli/releases).

### 3. Download the Script
Save `transifex-bulk-downloader.py` to your local machine.

## Configuration

### API Token
You'll need a Transifex API token with appropriate permissions. You can:
1. Set it as an environment variable: `TX_TOKEN` or `TRANSIFEX_API_TOKEN`
2. Enter it when prompted during script execution
3. Save it in the configuration file

### Configuration File
The script supports JSON configuration files. Create a `hybrid_config.json`:

```json
{
  "api_token": "YOUR_API_TOKEN_HERE",
  "organization_slug": "your-org-slug",
  "project_slugs": null,
  "output_directory": "./transifex_downloads",
  "download_mode": "both",
  "language_codes": null,
  "workers": 12,
  "skip_on_error": true,
  "use_silent_mode": false,
  "use_filtered_output": false,
  "file_filter": "<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>",
  "minimum_perc": 0,
  "force_download": false,
  "skip_existing_files": true,
  "add_remote_timeout": 300
}
```

### Configuration Options

| Option | Type | Default | Description |
|--------|---|---|-------------|
| `api_token` | string | - | Your Transifex API token |
| `organization_slug` | string | - | Your Transifex organization slug |
| `project_slugs` | array/null | null | List of specific project slugs to download (null = all projects) |
| `output_directory` | string/null | "./transifex_downloads" | Directory where files will be downloaded |
| `download_mode` | string | "both" | What to download: "source", "translations", or "both" |
| `language_codes` | array/null | null | List of language codes to download (null = all languages) |
| `workers` | integer | 12 | Number of concurrent download workers (max 30) |
| `skip_on_error` | boolean | true | Continue downloading if individual resources fail |
| `use_silent_mode` | boolean | false | Suppress detailed output |
| `use_filtered_output` | boolean | false | Show custom progress bars with filtered output |
| `file_filter` | string | `<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>` | Pattern for organizing downloaded files |
| `minimum_perc` | integer | 0 | Minimum translation completion percentage (0-100) |
| `force_download` | boolean | false | Force re-download of all files |
| `skip_existing_files` | boolean | true | Skip files that already exist locally |
| `add_remote_timeout` | integer | 300 | Timeout in seconds for adding remote projects |

### File Filter Pattern
The `file_filter` option controls how downloaded files are organized. Use these placeholders:
- `<project_slug>`: Project identifier
- `<resource_slug>`: Resource identifier
- `<lang>`: Language code
- `<ext>`: File extension

Example patterns:
- Default: `<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>`
- Flat structure: `translations/<project_slug>_<resource_slug>_<lang>.<ext>`
- By language: `<lang>/<project_slug>/<resource_slug>.<ext>`

## Usage

### Basic Usage
Run the script interactively:
```bash
python transifex-bulk-downloader.py
```

The script will guide you through:
1. Entering your API token (if not in environment)
2. Specifying your organization slug
3. Choosing what to download (source/translations/both)
4. Selecting specific projects or all projects
5. Choosing languages (for translations)
6. Setting the output directory

### Using a Configuration File
1. Create `hybrid_config.json` with your settings
2. Run the script:
```bash
python transifex-bulk-downloader.py
```
3. Choose to load the configuration when prompted

### Environment Variables
Set your API token to avoid entering it each time:
```bash
export TX_TOKEN="your_api_token_here"
python transifex-bulk-downloader.py
```

## Workflow

### 1. Discovery Phase
The script connects to the Transifex API and discovers all projects in your organization. If you've specified particular projects in the configuration, it filters to only those projects.

### 2. Configuration Generation
For each discovered project, the script:
- Uses `tx add remote` to generate proper `.tx/config` entries
- Handles special characters in resource names (replaces quotes with underscores to address potential issues in the config file)
- Creates a complete configuration file for the CLI

### 3. Download Phase
Using the generated configuration, the script:
- Executes `tx pull` with optimized settings
- Uses worker pools for parallel downloads
- Handles errors gracefully with `--skip` option by default

### 4. Reporting
After download completion, the script generates:
- Summary of downloaded files
- File type breakdown
- Total size information

## Advanced Features

### Incremental Downloads
The script intelligently handles existing configurations:
- Detects existing `.tx/config` files
- Shows configuration age and statistics
- Offers options to update, use as-is, or start fresh
- Identifies new projects not in existing config

### Performance Optimization
- Configurable worker pools (up to 30 concurrent downloads)
- Skip existing files to avoid re-downloads
- Efficient resource discovery
- Minimal API calls for project information

### Recommended Settings for Large Organizations
- Keep `workers` at 12 or lower to avoid API rate limits (1200 polling, 500 upload/download)
- Enable `skip_existing_files` for incremental updates
- Use `use_filtered_output` for cleaner progress tracking
- Set appropriate `add_remote_timeout` for slow connections (default 300s)

## Troubleshooting

### Common Issues

**"Transifex CLI not found"**
- Ensure the CLI is installed and in your PATH
- Try running `tx --version` to verify installation

**"API token is invalid"**
- Verify your token has the necessary permissions
- Check that the token hasn't expired
- Ensure you're using the correct organization slug

**"Projects not found"**
- Verify project slugs are spelled correctly
- Check that you have access to the projects
- Ensure projects exist in the specified organization

**Memory issues with large organizations**
- The script automatically limits file scanning to 100,000 items
- Log files rotate at 10MB to prevent disk space issues
- Consider downloading specific projects rather than entire organizations
- Use filtered output mode to reduce memory usage during downloads

**Timeout errors**
- Increase `add_remote_timeout` for slow connections
- Reduce the number of workers if hitting rate limits
- Check your internet connection stability

### Debug Information
The script creates detailed log files:
- `hybrid_download_YYYYMMDD_HHMMSS.log`: Full debug logs
- `download_report_YYYYMMDD_HHMMSS.txt`: Summary report

## Platform-Specific Notes

### Windows
- The script handles Windows paths correctly
- Uses Windows-compatible file locking mechanisms
- Progress bars may display differently in some terminals

### Linux/macOS
- Full support for all features
- Better progress bar rendering in most terminals
- Native file locking support

## Best Practices

1. **Start with a subset**: Test with a few projects before downloading everything
2. **Use incremental downloads**: Keep `skip_existing_files` enabled for faster subsequent runs
3. **Monitor the first run**: Watch for any authentication or permission issues
4. **Adjust workers**: Start with default (12) and adjust based on your connection
5. **Regular updates**: Periodically regenerate config to catch new projects

## Security Considerations

- API tokens are not saved in configuration files by default
- Use environment variables for token storage in automated scenarios
- Configuration files can be excluded from version control
- Tokens are masked in interactive prompts

## Support

For issues related to:
- The script itself: Check the generated log files for detailed error information
- Transifex API: Consult the [Transifex API documentation](https://developers.transifex.com/)
- Transifex CLI: See the [official CLI documentation](https://github.com/transifex/cli)

## Version History

- **1.0**: Initial release
