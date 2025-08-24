# 📦 Installation Guide

## 🚀 Quick Install (Recommended)

```bash
# Clone the repository
git clone https://github.com/yourusername/ai-codebase-indexer.git
cd ai-codebase-indexer

# Install with all features
pip install -r requirements-full.txt

# Test it works
python codebase_indexer.py . --summary
```

## 🎯 Minimal Install

If you don't need database analysis or enhanced parsing:

```bash
# Just download the main script
wget https://raw.githubusercontent.com/yourusername/ai-codebase-indexer/main/codebase_indexer.py

# Run directly (no dependencies needed)
python codebase_indexer.py ./my-project --summary
```

## 🛠️ Custom Installation

Install only what you need:

### Database Support Only
```bash
# MySQL projects
pip install mysql-connector-python

# PostgreSQL projects  
pip install psycopg2-binary

# SQLite is built into Python
```

### Enhanced Parsing Only
```bash
# Better React/PHP/JS analysis
pip install tree-sitter tree-sitter-languages
```

### Everything
```bash
pip install -r requirements-full.txt
```

## 🐍 Using pip install (Future)

Once published to PyPI:

```bash
# Basic installation
pip install ai-codebase-indexer

# With database support
pip install ai-codebase-indexer[mysql,postgresql]

# With enhanced parsing
pip install ai-codebase-indexer[enhanced]

# Everything
pip install ai-codebase-indexer[full]
```

## 🔧 Development Setup

```bash
# Fork and clone the repo
git clone https://github.com/yourusername/ai-codebase-indexer.git
cd ai-codebase-indexer

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode
pip install -e .
pip install -r requirements-full.txt

# Run tests (if available)
python -m pytest tests/
```

## 🚨 Troubleshooting

### Tree-sitter Installation Issues

**Problem:** `tree-sitter-languages` fails to install

**Solution:**
```bash
pip uninstall tree-sitter-languages
pip install tree-sitter
pip install tree-sitter-python tree-sitter-javascript tree-sitter-php
```

### Database Connection Issues

**MySQL:**
```bash
# On Ubuntu/Debian
sudo apt-get install default-libmysqlclient-dev
pip install mysql-connector-python

# On macOS
brew install mysql
pip install mysql-connector-python

# On Windows
pip install mysql-connector-python
```

**PostgreSQL:**
```bash
# On Ubuntu/Debian
sudo apt-get install libpq-dev
pip install psycopg2-binary

# On macOS
brew install postgresql
pip install psycopg2-binary

# On Windows
pip install psycopg2-binary
```

### Python Version Issues

**Problem:** Requires Python 3.8+

**Check version:**
```bash
python --version
# Should show 3.8 or higher
```

**Install Python 3.8+ if needed:**
- **Ubuntu/Debian:** `sudo apt install python3.9`
- **macOS:** `brew install python@3.9`
- **Windows:** Download from [python.org](https://python.org)

### Permission Issues

**Problem:** Permission denied when installing

**Solution:**
```bash
# Use virtual environment (recommended)
python -m venv venv
source venv/bin/activate
pip install -r requirements-full.txt

# Or install for user only
pip install --user -r requirements-full.txt
```

## ✅ Verify Installation

After installation, test with:

```bash
# Check if the script runs
python codebase_indexer.py --help

# Test basic analysis
python codebase_indexer.py . --summary

# Test database connection (if installed)
python codebase_indexer.py . \
  --db-type sqlite \
  --db-path ./test.db \
  --summary
```

## 📱 Platform-Specific Notes

### Windows
- Use `python` instead of `python3`
- Use `pip` instead of `pip3`
- Virtual environment: `venv\Scripts\activate`

### macOS
- May need to install Xcode command line tools: `xcode-select --install`
- Use Homebrew for system dependencies

### Linux
- Install system dependencies with your package manager
- Consider using virtual environments to avoid conflicts

## 🔄 Updating

```bash
# Pull latest changes
git pull origin main

# Update dependencies
pip install -r requirements-full.txt --upgrade

# Check version
python codebase_indexer.py --help
```

## 🆘 Getting Help

1. **Check the main [README.md](README.md)** for usage examples
2. **Search existing [GitHub issues](https://github.com/yourusername/ai-codebase-indexer/issues)**
3. **Create a new issue** with:
   - Your Python version (`python --version`)
   - Your OS and version
   - Error messages and stack traces
   - Steps to reproduce

---

**Still stuck?** Open an issue and we'll help you out!