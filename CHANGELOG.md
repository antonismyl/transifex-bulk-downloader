# Changelog

## [2.1.1] - 2024-09-02

### Added
- Configuration choice prompt when existing config is found
- Users can now choose to use existing configuration or create new one
- Displays current organization and output directory before choice

### Improved  
- Simplified from 3 options to 2 clear choices: use existing or start fresh
- Better UX for users working with multiple organizations

## [2.1.0] - 2024-09-01

### Changed
- Simplified initial configuration setup - removed download mode, translation mode, project filtering, and language filtering from config
- Moved download-specific choices to execution time for better flexibility
- Users now configure once and make download choices each time they run the tool

### Improved
- Better separation between persistent configuration and runtime options
- More intuitive workflow - configure API token/org once, choose download options per run
- Updated main menu text from "Translation files" to "Source/Translation files"

### Technical
- Streamlined Config dataclass to focus on persistent settings only
- Reduced configuration complexity by 41 lines of code