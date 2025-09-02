# Transifex Bulk Downloader

A simplified, reliable tool for bulk downloading translation files and TMX (Translation Memory) files from Transifex using the official CLI and Python SDK.

## 🌟 Features

### 📁 **Translation File Downloads**
- **Simplified configuration**: Configure once, choose download options per run
- **Flexible download modes**: Source only, translations only, or both
- **Translation filtering**: 9 different translation download modes
- **Language selection**: All languages or specific language codes
- **Project filtering**: All organization projects or specific projects
- **Configurable workers**: 1-30 parallel workers for optimal performance
- **Smart error handling**: Treats partial downloads as successful when files are downloaded

### 🗂️ **TMX Downloads**
- **Three export options**:
  - One file per project (all languages combined)
  - Separate files per language (all languages)
  - Separate files for specific languages
- **Project selection**: All projects or specific projects
- **Direct Python SDK integration** for reliable downloads
- **Clean progress tracking** with single-line updates

### 🔧 **Smart Configuration**
- **Automatic CLI authentication** via local `.transifexrc` creation
- **Organized file structure** with dedicated subdirectories
- **Config persistence** for basic settings (API token, organization)
- **No complex validation** - trusts CLI-generated configurations

## 🚀 Quick Start

### Prerequisites

1. **Install Transifex CLI**:
   ```bash
   curl -o- https://raw.githubusercontent.com/transifex/cli/master/install.sh | bash
   ```
   Or download from: https://github.com/transifex/cli/releases

2. **Install Python dependencies**:
   ```bash
   pip install transifex-python requests
   ```

3. **Get your Transifex API token**:
   - Go to Transifex → User Settings → API Token
   - Generate a new token (save it securely - shown only once)

### Usage

1. **Run the script**:
   ```bash
   python transifex-bulk-downloader.py
   ```

2. **Choose operation type**:
   ```
   📋 What would you like to download?
     [1] Source/Translation files
     [2] Translation Memory files
   ```

3. **Follow the interactive prompts** for your chosen operation

4. **Files will be organized in**:
   ```
   transifex_downloads/
   ├── .transifexrc          # Local authentication
   ├── .tx/config            # CLI configuration  
   ├── files/                # Downloaded translation files
   └── TMX files/            # Downloaded TMX files
   ```

## 📋 Configuration

### Initial Setup

On first run, you'll be prompted to configure basic settings:

- **API Token**: Your Transifex API token
- **Organization Slug**: Your Transifex organization identifier
- **Output Directory**: Where to store downloads (optional)

These settings are saved to `bulk_download_config.json` for reuse.

### Per-Session Options

Each time you run file downloads, you'll be prompted for:

- **Projects**: All organization projects or specific projects
- **Download Mode**: Source files, translations, or both
- **Translation Mode**: Default, reviewed, proofread, etc.
- **Languages**: All languages or specific language codes  
- **Workers**: Number of parallel downloads (1-30, default 12)

## 🎯 Translation Modes

When downloading translations, choose from these modes:

| Mode | Description |
|------|-------------|
| `default` | Standard translations |
| `reviewed` | Only reviewed translations |
| `proofread` | Only proofread translations |
| `translator` | Suitable for offline translation |
| `untranslated` | Only untranslated strings |
| `onlytranslated` | Only translated strings |
| `onlyreviewed` | Only reviewed strings |
| `onlyproofread` | Only proofread strings |
| `sourceastranslation` | Use source as translation |

## 📁 File Organization

### Translation Files
Files are organized using the pattern:
```
files/<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>
```

Example:
```
files/
├── mobile-app/
│   ├── strings/
│   │   ├── strings_en.xml
│   │   ├── strings_es.xml
│   │   └── strings_fr.xml
│   └── dialogs/
│       ├── dialogs_en.xml
│       └── dialogs_es.xml
└── website/
    └── main/
        ├── main_en.json
        └── main_es.json
```

### TMX Files
TMX files are named based on your selection:
- **All languages**: `project_slug_all_languages.tmx`
- **Per language**: `project_slug_language_code.tmx`

## 🔧 Advanced Usage

### Environment Variables

You can set these environment variables to skip prompts:

```bash
export TX_TOKEN="your_api_token"
export TRANSIFEX_API_TOKEN="your_api_token"  # Alternative
```

### Reusing Existing Configuration

The script detects existing `.tx/config` files and offers to reuse them:

```
📁 Found existing .tx/config in /path/to/transifex_downloads
Use existing config? [Y/n]:
```

Choose **Y** to skip project configuration and use existing settings.

### Configuration File Format

`bulk_download_config.json`:
```json
{
  "api_token": "your_token_here",
  "organization_slug": "your_org",
  "output_directory": "/path/to/downloads",
  "workers": 12,
  "file_filter": "files/<project_slug>/<resource_slug>/<resource_slug>_<lang>.<ext>"
}
```

## 🐛 Troubleshooting

### Authentication Issues

**403 Forbidden errors**:
- Ensure your API token is valid and has necessary permissions
- Check that the token belongs to the correct organization
- The script creates a local `.transifexrc` to avoid conflicts

### Download Issues

**No files in expected location**:
- Files go to `files/` subdirectory within the working directory
- Check the file filter pattern in your configuration
- Verify the CLI configuration was generated correctly

### Performance Optimization

**Slow downloads**:
- Reduce worker count (try 6-8 instead of 12)
- Check if your network or API rate limits are being hit
- Use translation modes to download only what you need

**Large organizations**:
- Use specific project filtering instead of all projects
- Consider downloading specific languages instead of all languages
- Break large downloads into smaller batches

## Version History

- **1.0**: Initial release
- **2.0**: Major Rewrite - Simplified Architecture, Added support for TMX file downloads, added option to select download modes. Removed experimental features.
- **2.1**: Enhanced UX - Moved download options from configuration to execution time for better flexibility
