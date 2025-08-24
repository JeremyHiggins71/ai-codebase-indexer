# Contributing to AI Codebase Indexer

Thank you for your interest in contributing! This project aims to make AI interactions with codebases more efficient and effective.

## 🚀 Quick Start for Contributors

1. Fork the repository
2. Clone your fork: `git clone https://github.com/yourusername/ai-codebase-indexer.git`
3. Create a virtual environment: `python -m venv venv && source venv/bin/activate`
4. Install development dependencies: `pip install -r requirements-full.txt`
5. Create a feature branch: `git checkout -b feature-name`
6. Make your changes
7. Test your changes (see Testing section below)
8. Submit a pull request

## 🎯 Ways to Contribute

### 1. New Language Support
Add parsing support for languages like:
- **Go** (growing in popularity)
- **Rust** (systems programming)
- **Swift** (iOS development)
- **Kotlin** (Android/backend)
- **C#** (enterprise applications)

**Implementation steps:**
1. Add language to `language_map` in `CodebaseIndexer.__init__()`
2. Create a parser method (e.g., `analyze_go_file()`)
3. Add tree-sitter support if available
4. Test with real codebases

### 2. Database Support
Add support for:
- **MongoDB** (document databases)
- **Redis** (key-value analysis)
- **SQLServer** (enterprise databases)

### 3. Enhanced Smart Filtering
Improve vendor detection for:
- Framework-specific patterns (Angular, Vue, Svelte)
- Language-specific package managers (Composer, NPM, Cargo)
- Cloud provider SDKs (AWS, Google Cloud, Azure)

### 4. Performance Improvements
- Parallel file processing
- Memory usage optimization
- Better caching strategies

### 5. Output Formats
- **YAML** output option
- **Markdown** documentation generator
- **GraphQL** schema generation

## 🧪 Testing

### Manual Testing
```bash
# Test with different project types
python codebase_indexer.py ./test-projects/react-app --summary
python codebase_indexer.py ./test-projects/laravel-app --summary
python codebase_indexer.py ./test-projects/django-app --summary

# Test database integration
python codebase_indexer.py ./test-projects/sample-app \
  --db-type sqlite \
  --db-path ./test.db \
  --summary
```

### Unit Tests (Future Enhancement)
We'd love to add a proper test suite! Priority areas:
- Vendor detection accuracy
- Parsing correctness for each language
- Cache invalidation logic
- Database schema extraction

## 📝 Code Style

### Python Style
- Follow PEP 8
- Use type hints where helpful
- Keep functions focused and small
- Add docstrings for public methods

### Example:
```python
def analyze_go_file(self, file_path: Path) -> FileInfo:
    """Analyze Go files for packages, functions, and structs."""
    try:
        # Implementation here
        pass
    except Exception as e:
        print(f"Error analyzing Go file {file_path}: {e}")
        return self._create_basic_file_info(file_path, 'go')
```

## 🐛 Bug Reports

When reporting bugs, please include:

1. **Environment details:**
   - Python version
   - Operating system
   - Installed dependencies (`pip list`)

2. **Reproduction steps:**
   - Exact command run
   - Project structure being analyzed
   - Expected vs actual behavior

3. **Error output:**
   - Full error messages
   - Stack traces

4. **Sample files:**
   - If possible, share minimal examples that trigger the issue

## 💡 Feature Requests

Before submitting feature requests:

1. Check existing issues and discussions
2. Consider if it fits the project's scope (AI-friendly codebase analysis)
3. Think about implementation complexity vs benefit
4. Consider if it could be a plugin/extension

**Good feature requests include:**
- Clear use case description
- Examples of how it would work
- Consideration of edge cases
- Willingness to help implement

## 🏗️ Architecture Notes

### Core Components

1. **File Scanners** (`scan_directory()`)
   - Handles ignore patterns and smart filtering
   - Manages caching and change detection

2. **Language Analyzers** (`analyze_*_file()` methods)
   - Extract language-specific structures
   - Use tree-sitter when available, regex as fallback

3. **Database Analyzers** (`_analyze_*_schema()` methods)
   - Connect to databases and extract schema
   - Generate sample data for context

4. **Output Generator** (`create_summary()`)
   - Transforms parsed data into AI-friendly format
   - Balances completeness with token efficiency

### Design Principles

- **Fail gracefully**: Don't crash on parsing errors
- **Token efficiency**: Optimize output for AI consumption
- **Performance first**: Smart caching and vendor filtering
- **Extensibility**: Easy to add new languages/databases

## 🔄 Release Process

1. Update version in `setup.py`
2. Update `CHANGELOG.md` (if we add one)
3. Tag release: `git tag v1.x.x`
4. Push tags: `git push origin --tags`

## 🤝 Community Guidelines

- Be respectful and constructive
- Focus on improving AI-codebase interaction
- Share real-world usage examples
- Help newcomers get started
- Consider performance impact of changes

## 📚 Resources

- [Tree-sitter Documentation](https://tree-sitter.github.io/tree-sitter/)
- [Database Schema Standards](https://en.wikipedia.org/wiki/Database_schema)
- [AI Token Usage Best Practices](https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count-them)

---

**Questions?** Open an issue or start a discussion. We're here to help!