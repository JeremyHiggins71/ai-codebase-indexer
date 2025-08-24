#!/usr/bin/env python3
"""
Codebase Indexer - Creates AI-friendly summaries of codebases
Analyzes code structure and dependencies without wasting tokens on implementation details.
Supports React, PHP, and MySQL schema analysis.
"""

import os
import ast
import json
import re
import argparse
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, asdict

# Database connectors (optional imports)
try:
    import mysql.connector
    MYSQL_AVAILABLE = True
except ImportError:
    MYSQL_AVAILABLE = False

try:
    import psycopg2
    import psycopg2.extras
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

# Enhanced parsing (optional imports)
try:
    import tree_sitter
    import tree_sitter_python
    import tree_sitter_javascript
    import tree_sitter_php
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False


@dataclass
class FileMetadata:
    """Metadata for tracking file changes and caching analysis results."""
    path: str
    mtime: float  # modification timestamp
    size: int
    checksum: str  # MD5 hash for content verification

@dataclass
class FunctionInfo:
    name: str
    args: List[str]
    docstring: Optional[str] = None
    decorators: List[str] = None
    line_number: int = 0
    return_type: Optional[str] = None  # For TypeScript/PHP

@dataclass
class ClassInfo:
    name: str
    methods: List[FunctionInfo]
    bases: List[str]
    docstring: Optional[str] = None
    line_number: int = 0

@dataclass
class ReactComponentInfo:
    name: str
    props: List[str]
    hooks: List[str]
    exports: str  # default, named, both
    is_functional: bool = True
    line_number: int = 0
    prop_types: Dict[str, str] = None  # For TypeScript props

@dataclass
class PHPClassInfo:
    name: str
    namespace: Optional[str]
    methods: List[FunctionInfo]
    properties: List[str]
    extends: Optional[str] = None
    implements: List[str] = None
    line_number: int = 0

@dataclass
class FileInfo:
    path: str
    language: str
    imports: List[str]
    functions: List[FunctionInfo]
    classes: List[ClassInfo]
    variables: List[str]
    docstring: Optional[str] = None
    loc: int = 0  # lines of code
    # React-specific
    components: List[ReactComponentInfo] = None
    # PHP-specific  
    php_classes: List[PHPClassInfo] = None
    namespace: Optional[str] = None
    # Metadata for caching
    metadata: FileMetadata = None
    
    def __post_init__(self):
        if self.components is None:
            self.components = []
        if self.php_classes is None:
            self.php_classes = []

@dataclass
class DatabaseColumn:
    name: str
    type: str
    nullable: bool
    default: Optional[str] = None
    key: Optional[str] = None  # PRI, UNI, MUL

@dataclass
class DatabaseTable:
    name: str
    columns: List[DatabaseColumn]
    foreign_keys: List[Dict[str, str]]
    indexes: List[str]
    sample_data: List[Dict] = None


class CodebaseIndexer:
    def __init__(self, root_path: str, db_config: Optional[Dict] = None, db_type: str = 'mysql', 
                 cache_file: str = '.codebase_cache.json', force_refresh: bool = False):
        self.root_path = Path(root_path)
        self.files: List[FileInfo] = []
        self.db_config = db_config
        self.db_type = db_type.lower()
        self.database_schema: List[DatabaseTable] = []
        self.cache_file = Path(root_path) / cache_file
        self.force_refresh = force_refresh
        self.file_cache: Dict[str, FileInfo] = {}
        
        # Load existing cache
        self._load_cache()
        
        # Initialize tree-sitter parsers if available
        self.parsers = {}
        if TREE_SITTER_AVAILABLE:
            self._init_tree_sitter_parsers()
        
        # Default ignore patterns - feel free to customize
        self.ignore_patterns = {
            # Directories
            'node_modules', '__pycache__', '.git', '.venv', 'venv', 'env',
            'dist', 'build', 'target', '.next', '.nuxt', 'coverage',
            '.pytest_cache', '.mypy_cache', '.tox', 'htmlcov', 'vendor',
            
            # Files
            '*.pyc', '*.pyo', '*.pyd', '*.so', '*.dll', '*.dylib',
            '*.log', '*.tmp', '*.temp', '*.bak', '*.swp', '*.swo',
            '.DS_Store', 'Thumbs.db', '*.min.js', '*.min.css',
            'package-lock.json', 'yarn.lock', 'poetry.lock', 'composer.lock'
        }

    def _init_tree_sitter_parsers(self) -> None:
        """Initialize tree-sitter parsers for better code analysis."""
        try:
            # Python parser
            self.parsers['python'] = tree_sitter.Parser()
            self.parsers['python'].set_language(tree_sitter_python.language())
            
            # JavaScript/TypeScript parser
            self.parsers['javascript'] = tree_sitter.Parser()
            self.parsers['javascript'].set_language(tree_sitter_javascript.language())
            
            # PHP parser
            self.parsers['php'] = tree_sitter.Parser()
            self.parsers['php'].set_language(tree_sitter_php.language())
            
            print("🚀 Tree-sitter parsers initialized for enhanced parsing")
        except Exception as e:
            print(f"⚠️  Tree-sitter initialization failed: {e}")
            print("💡 Install with: pip install tree-sitter tree-sitter-languages")

    def _load_cache(self) -> None:
        """Load existing cache file if it exists."""
        if self.cache_file.exists() and not self.force_refresh:
            try:
                with open(self.cache_file, 'r') as f:
                    cache_data = json.load(f)
                    
                # Convert cached data back to FileInfo objects
                for file_data in cache_data.get('files', []):
                    # Reconstruct the FileInfo object
                    file_info = self._dict_to_file_info(file_data)
                    self.file_cache[file_info.path] = file_info
                    
                print(f"📋 Loaded cache with {len(self.file_cache)} files")
            except Exception as e:
                print(f"⚠️  Cache loading failed: {e}")

    def _save_cache(self) -> None:
        """Save current analysis to cache file."""
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'files': [self._file_info_to_dict(f) for f in self.files]
            }
            
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2, default=str)
                
        except Exception as e:
            print(f"⚠️  Cache saving failed: {e}")

    def _file_info_to_dict(self, file_info: FileInfo) -> Dict:
        """Convert FileInfo to dictionary for caching."""
        result = asdict(file_info)
        # Handle nested dataclasses
        if file_info.metadata:
            result['metadata'] = asdict(file_info.metadata)
        return result

    def _dict_to_file_info(self, data: Dict) -> FileInfo:
        """Convert dictionary back to FileInfo object."""
        # Handle metadata separately
        metadata = None
        if data.get('metadata'):
            metadata = FileMetadata(**data['metadata'])
            
        # Remove metadata from main data before creating FileInfo
        file_data = {k: v for k, v in data.items() if k != 'metadata'}
        
        # Convert nested structures back to dataclasses
        if file_data.get('functions'):
            file_data['functions'] = [FunctionInfo(**f) for f in file_data['functions']]
        if file_data.get('classes'):
            file_data['classes'] = [ClassInfo(
                name=c['name'],
                methods=[FunctionInfo(**m) for m in c.get('methods', [])],
                bases=c.get('bases', []),
                docstring=c.get('docstring'),
                line_number=c.get('line_number', 0)
            ) for c in file_data['classes']]
        if file_data.get('components'):
            file_data['components'] = [ReactComponentInfo(**c) for c in file_data['components']]
        if file_data.get('php_classes'):
            file_data['php_classes'] = [PHPClassInfo(
                name=c['name'],
                namespace=c.get('namespace'),
                methods=[FunctionInfo(**m) for m in c.get('methods', [])],
                properties=c.get('properties', []),
                extends=c.get('extends'),
                implements=c.get('implements', []),
                line_number=c.get('line_number', 0)
            ) for c in file_data['php_classes']]
            
        file_info = FileInfo(**file_data)
        file_info.metadata = metadata
        return file_info

    def _get_file_metadata(self, file_path: Path) -> FileMetadata:
        """Get file metadata for change detection."""
        stat = file_path.stat()
        
        # Calculate MD5 checksum for content verification
        with open(file_path, 'rb') as f:
            content = f.read()
            checksum = hashlib.md5(content).hexdigest()
        
        return FileMetadata(
            path=str(file_path.relative_to(self.root_path)),
            mtime=stat.st_mtime,
            size=stat.st_size,
            checksum=checksum
        )

    def _should_reanalyze_file(self, file_path: Path) -> bool:
        """Check if file needs re-analysis based on metadata."""
        if self.force_refresh:
            return True
            
        relative_path = str(file_path.relative_to(self.root_path))
        cached_file = self.file_cache.get(relative_path)
        
        if not cached_file or not cached_file.metadata:
            return True
        
        current_metadata = self._get_file_metadata(file_path)
        cached_metadata = cached_file.metadata
        
        # Check if file has changed
        return (current_metadata.mtime != cached_metadata.mtime or 
                current_metadata.size != cached_metadata.size or
                current_metadata.checksum != cached_metadata.checksum)
        
        # Language mappings
        self.language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'react',
            '.tsx': 'react_ts',
            '.php': 'php',
            '.java': 'java',
            '.cpp': 'cpp',
            '.c': 'c',
            '.h': 'c_header',
            '.cs': 'csharp',
            '.go': 'go',
            '.rs': 'rust',
            '.rb': 'ruby',
            '.swift': 'swift',
            '.kt': 'kotlin',
            '.scala': 'scala',
            '.sql': 'sql',
            '.sh': 'shell',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.json': 'json',
            '.xml': 'xml',
            '.html': 'html',
            '.css': 'css',
            '.md': 'markdown',
            '.dockerfile': 'dockerfile',
            '.toml': 'toml'
        }

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored based on patterns."""
        path_str = str(path)
        name = path.name
        
        for pattern in self.ignore_patterns:
            if pattern.startswith('*.'):
                if name.endswith(pattern[1:]):
                    return True
            elif pattern in path_str or name == pattern:
                return True
        return False

    def get_language(self, file_path: Path) -> Optional[str]:
        """Determine the programming language based on file extension."""
        return self.language_map.get(file_path.suffix.lower())

    def analyze_python_file(self, file_path: Path) -> FileInfo:
        """Analyze a Python file using AST."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            tree = ast.parse(content)
            
            imports = []
            functions = []
            classes = []
            variables = []
            file_docstring = None
            
            # Get module docstring
            if (tree.body and isinstance(tree.body[0], ast.Expr) 
                and isinstance(tree.body[0].value, ast.Constant)):
                file_docstring = tree.body[0].value.value

            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.Import):
                        imports.extend([alias.name for alias in node.names])
                    else:
                        module = node.module or ''
                        names = [alias.name for alias in node.names]
                        imports.append(f"{module}.{','.join(names)}" if module else ','.join(names))
                
                elif isinstance(node, ast.FunctionDef):
                    # Skip nested functions for top-level analysis
                    if self._is_top_level(node, tree):
                        func = FunctionInfo(
                            name=node.name,
                            args=[arg.arg for arg in node.args.args],
                            docstring=ast.get_docstring(node),
                            decorators=[d.id if isinstance(d, ast.Name) else str(d) for d in node.decorator_list],
                            line_number=node.lineno
                        )
                        functions.append(func)
                
                elif isinstance(node, ast.ClassDef):
                    if self._is_top_level(node, tree):
                        methods = []
                        for item in node.body:
                            if isinstance(item, ast.FunctionDef):
                                method = FunctionInfo(
                                    name=item.name,
                                    args=[arg.arg for arg in item.args.args],
                                    docstring=ast.get_docstring(item),
                                    decorators=[d.id if isinstance(d, ast.Name) else str(d) for d in item.decorator_list],
                                    line_number=item.lineno
                                )
                                methods.append(method)
                        
                        cls = ClassInfo(
                            name=node.name,
                            methods=methods,
                            bases=[base.id if isinstance(base, ast.Name) else str(base) for base in node.bases],
                            docstring=ast.get_docstring(node),
                            line_number=node.lineno
                        )
                        classes.append(cls)
                
                elif isinstance(node, ast.Assign):
                    # Top-level variable assignments
                    if self._is_top_level(node, tree):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                variables.append(target.id)

            metadata = self._get_file_metadata(file_path)

            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language='python',
                imports=imports,
                functions=functions,
                classes=classes,
                variables=variables,
                docstring=file_docstring,
                metadata=metadata,
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return self._create_basic_file_info(file_path, 'python')

    def _is_top_level(self, node: ast.AST, tree: ast.AST) -> bool:
        """Check if a node is at the top level of the module."""
        return node in tree.body

    def analyze_php_file(self, file_path: Path) -> FileInfo:
        """Analyze PHP files for classes, functions, and namespaces."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract namespace
            namespace_match = re.search(r'namespace\s+([^;]+);', content)
            namespace = namespace_match.group(1) if namespace_match else None
            
            # Extract imports/uses
            imports = []
            use_matches = re.findall(r'use\s+([^;]+);', content)
            for use in use_matches:
                imports.append(use.strip())
            
            require_matches = re.findall(r'(?:require|include)(?:_once)?\s*\(?[\'"]([^\'"]+)[\'"]', content)
            imports.extend(require_matches)
            
            # Extract functions
            functions = []
            func_matches = re.finditer(r'function\s+(\w+)\s*\(([^)]*)\)', content)
            for match in func_matches:
                line_number = content[:match.start()].count('\n') + 1
                args = [arg.strip().split()[-1].lstrip('
        """Basic analysis of JavaScript/TypeScript files using regex."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract imports/requires
            imports = []
            import_patterns = [
                r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
                r'import\s+[\'"]([^\'"]+)[\'"]',
                r'require\([\'"]([^\'"]+)[\'"]\)',
                r'from\s+[\'"]([^\'"]+)[\'"]\s+import'
            ]
            
            for pattern in import_patterns:
                imports.extend(re.findall(pattern, content))
            
            # Extract function declarations
            functions = []
            func_patterns = [
                r'function\s+(\w+)\s*\(',
                r'const\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'let\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'var\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'(\w+)\s*:\s*(?:async\s+)?function',
                r'async\s+function\s+(\w+)\s*\('
            ]
            
            for pattern in func_patterns:
                functions.extend(re.findall(pattern, content))
            
            # Extract class declarations
            classes = []
            class_matches = re.findall(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content)
            
            lang = self.get_language(file_path)
            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language=lang,
                imports=list(set(imports)),
                functions=[FunctionInfo(name=f, args=[]) for f in set(functions)],
                classes=[ClassInfo(name=c[0], methods=[], bases=[c[1]] if c[1] else []) for c in classes],
                variables=[],
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return self._create_basic_file_info(file_path, self.get_language(file_path))

    def _create_basic_file_info(self, file_path: Path, language: str) -> FileInfo:
        """Create minimal file info when parsing fails."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                loc = len(f.readlines())
        except:
            loc = 0
        
        metadata = self._get_file_metadata(file_path)
            
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=language,
            imports=[],
            functions=[],
            classes=[],
            variables=[],
            components=[],
            php_classes=[],
            metadata=metadata,
            loc=loc
        )

    def scan_directory(self) -> None:
        """Scan the directory and analyze all supported files."""
        for file_path in self.root_path.rglob('*'):
            if file_path.is_file() and not self.should_ignore(file_path):
                language = self.get_language(file_path)
                
                if language:
                    if language == 'python':
                        file_info = self.analyze_python_file(file_path)
                    elif language in ['javascript', 'typescript', 'jsx', 'tsx']:
                        file_info = self.analyze_javascript_file(file_path)
                    else:
                        file_info = self._create_basic_file_info(file_path, language)
                    
                    self.files.append(file_info)

    def generate_dependency_map(self) -> Dict[str, List[str]]:
        """Generate a dependency map showing which files import from which."""
        dep_map = {}
        
        for file_info in self.files:
            deps = []
            for imp in file_info.imports:
                # Try to resolve relative imports to actual files
                resolved = self._resolve_import(imp, file_info.path)
                if resolved:
                    deps.append(resolved)
                else:
                    deps.append(imp)  # External dependency
            dep_map[file_info.path] = deps
            
        return dep_map

    def _resolve_import(self, import_name: str, current_file: str) -> Optional[str]:
        """Try to resolve imports to actual files in the codebase."""
        # This is simplified - you might want to enhance this for your specific needs
        for file_info in self.files:
            file_name = Path(file_info.path).stem
            if import_name.endswith(file_name) or file_name in import_name:
                return file_info.path
    def _extract_react_components(self, content: str) -> List[ReactComponentInfo]:
        """Extract React components from file content (regex fallback)."""
        components = []
        
        # Functional components patterns
        patterns = [
            # const Component = () => { ... }
            r'const\s+(\w+)\s*=\s*\([^)]*\)\s*=>\s*{',
            # function Component() { ... }
            r'function\s+(\w+)\s*\([^)]*\)\s*{(?:[^}]|{[^}]*})*return\s*\(',
            # export default function Component
            r'export\s+default\s+function\s+(\w+)',
            # export function Component
            r'export\s+function\s+(\w+)',
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, content, re.MULTILINE | re.DOTALL)
            for match in matches:
                comp_name = match.group(1)
                if comp_name[0].isupper():  # React components start with uppercase
                    line_number = content[:match.start()].count('\n') + 1
                    
                    # Extract props (basic detection)
                    props = self._extract_component_props(content, comp_name, match.start())
                    
                    # Extract hooks
                    hooks = self._extract_component_hooks(content, comp_name, match.start())
                    
                    # Determine export type
                    exports = self._get_export_type(content, comp_name)
                    
                    components.append(ReactComponentInfo(
                        name=comp_name,
                        props=props,
                        hooks=hooks,
                        exports=exports,
                        line_number=line_number
                    ))
        
        return components

    def create_summary(self) -> Dict[str, Any]:
        """Create the AI-friendly summary."""
        # Get overview stats
        total_files = len(self.files)
        total_loc = sum(f.loc for f in self.files)
        languages = set(f.language for f in self.files)
        
        # Count by language
        lang_stats = {}
        for lang in languages:
            files = [f for f in self.files if f.language == lang]
            lang_stats[lang] = {
                'files': len(files),
                'loc': sum(f.loc for f in files)
            }
        
        # Get all unique external dependencies
        external_deps = set()
        for file_info in self.files:
            for imp in file_info.imports:
                if not any(imp in f.path for f in self.files):
                    # Clean up the import name
                    clean_imp = imp.split('.')[0] if '.' in imp else imp
                    external_deps.add(clean_imp)
        
        # Create file summaries (the meat of the index)
        file_summaries = []
        for file_info in self.files:
            summary = {
                'path': file_info.path,
                'language': file_info.language,
                'loc': file_info.loc
            }
            
            if file_info.docstring:
                summary['description'] = file_info.docstring[:200]  # Truncate for token efficiency
            
            if file_info.imports:
                summary['imports'] = file_info.imports[:10]  # Limit for token efficiency
            
            if file_info.functions:
                summary['functions'] = [
                    {
                        'name': f.name,
                        'args': f.args,
                        'doc': f.docstring[:100] if f.docstring else None
                    } for f in file_info.functions[:20]  # Limit functions shown
                ]
            
            if file_info.classes:
                summary['classes'] = [
                    {
                        'name': c.name,
                        'bases': c.bases,
                        'methods': [m.name for m in c.methods[:15]],  # Just method names
                        'doc': c.docstring[:100] if c.docstring else None
                    } for c in file_info.classes[:10]  # Limit classes shown
                ]
            
            # React components
            if file_info.components:
                summary['react_components'] = [
                    {
                        'name': c.name,
                        'props': c.props,
                        'hooks': c.hooks,
                        'export_type': c.exports
                    } for c in file_info.components[:15]
                ]
            
            # PHP classes
            if file_info.php_classes:
                summary['php_classes'] = [
                    {
                        'name': c.name,
                        'namespace': c.namespace,
                        'extends': c.extends,
                        'implements': c.implements,
                        'methods': [m.name for m in c.methods[:15]],
                        'properties': c.properties[:10]
                    } for c in file_info.php_classes[:10]
                ]
            
            if file_info.namespace:
                summary['namespace'] = file_info.namespace
            
            if file_info.variables:
                summary['key_variables'] = file_info.variables[:10]  # Top-level vars only
                
            file_summaries.append(summary)
        
        result = {
            'project_overview': {
                'total_files': total_files,
                'total_loc': total_loc,
                'languages': lang_stats,
                'external_dependencies': sorted(list(external_deps))
            },
            'dependency_map': self.generate_dependency_map(),
            'files': file_summaries
        }
        
        # Add database schema if available
        if self.database_schema:
            result['database_schema'] = [
                {
                    'table': table.name,
                    'columns': [
                        {
                            'name': col.name,
                            'type': col.type,
                            'nullable': col.nullable,
                            'key': col.key
                        } for col in table.columns
                    ],
                    'foreign_keys': table.foreign_keys,
                    'indexes': table.indexes,
                    'sample_records': len(table.sample_data) if table.sample_data else 0
                } for table in self.database_schema
            ]
        
        return result

    def save_index(self, output_path: str = 'codebase_index.json') -> None:
        """Save the index to a JSON file."""
        summary = self.create_summary()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        
        file_size_kb = os.path.getsize(output_path) / 1024
        cache_size_kb = os.path.getsize(self.cache_file) / 1024 if self.cache_file.exists() else 0
        
        print(f"📚 Codebase index saved to {output_path}")
        print(f"📊 Analyzed {len(self.files)} files ({sum(f.loc for f in self.files):,} lines)")
        print(f"🎯 Index size: {file_size_kb:.1f}KB, Cache size: {cache_size_kb:.1f}KB")
        print(f"⚡ Next run will be much faster thanks to caching!")

    def add_ignore_pattern(self, pattern: str) -> None:
        """Add a custom ignore pattern."""
        self.ignore_patterns.add(pattern)

    def remove_ignore_pattern(self, pattern: str) -> None:
        """Remove an ignore pattern."""
        self.ignore_patterns.discard(pattern)

    def print_summary(self) -> None:
        """Print a quick summary to console."""
        summary = self.create_summary()
        overview = summary['project_overview']
        
        print("\n🔍 CODEBASE ANALYSIS SUMMARY")
        print("=" * 40)
        print(f"Total Files: {overview['total_files']}")
        print(f"Total Lines: {overview['total_loc']:,}")
        
        # Show parsing method used
        parsing_method = "Tree-sitter + AST" if TREE_SITTER_AVAILABLE else "Regex + AST"
        print(f"Parsing Method: {parsing_method}")
        
        print(f"\nLanguage Breakdown:")
        for lang, stats in overview['languages'].items():
            print(f"  {lang}: {stats['files']} files ({stats['loc']:,} lines)")
        
        # React component summary
        react_files = [f for f in self.files if f.language in ['react', 'react_ts'] and f.components]
        if react_files:
            total_components = sum(len(f.components) for f in react_files)
            print(f"\n⚛️  React Components: {total_components}")
            
            # Show hooks usage
            all_hooks = set()
            for f in react_files:
                for c in f.components:
                    all_hooks.update(c.hooks)
            if all_hooks:
                print(f"  Common hooks: {', '.join(sorted(list(all_hooks))[:8])}")
        
        # PHP summary
        php_files = [f for f in self.files if f.language == 'php' and f.php_classes]
        if php_files:
            total_php_classes = sum(len(f.php_classes) for f in php_files)
            namespaces = set(f.namespace for f in php_files if f.namespace)
            print(f"\n🐘 PHP Classes: {total_php_classes}")
            if namespaces:
                print(f"  Namespaces: {', '.join(sorted(namespaces))}")
        
        # Database summary
        if self.database_schema:
            print(f"\n🗄️  Database Tables: {len(self.database_schema)}")
            tables_with_fks = len([t for t in self.database_schema if t.foreign_keys])
            print(f"  Tables with relationships: {tables_with_fks}")
        
        print(f"\nKey Dependencies:")
        deps = overview['external_dependencies'][:10]  # Show top 10
        for dep in deps:
            print(f"  • {dep}")
        
        if len(overview['external_dependencies']) > 10:
            print(f"  ... and {len(overview['external_dependencies']) - 10} more")


def main():
    parser = argparse.ArgumentParser(description='Analyze codebase and create AI-friendly index')
    parser.add_argument('path', help='Path to codebase root directory')
    parser.add_argument('-o', '--output', default='codebase_index.json', 
                       help='Output file name (default: codebase_index.json)')
    parser.add_argument('--ignore', action='append', 
                       help='Additional patterns to ignore (can be used multiple times)')
    parser.add_argument('--summary', action='store_true', 
                       help='Print summary to console')
    parser.add_argument('--force-refresh', action='store_true',
                       help='Force re-analysis of all files (ignore cache)')
    parser.add_argument('--cache-file', default='.codebase_cache.json',
                       help='Cache file name (default: .codebase_cache.json)')
    
    # Database options
    parser.add_argument('--db-type', choices=['mysql', 'postgresql', 'sqlite'], 
                       default='mysql', help='Database type (default: mysql)')
    parser.add_argument('--db-host', help='Database host (default: localhost)', default='localhost')
    parser.add_argument('--db-port', help='Database port', type=int)
    parser.add_argument('--db-user', help='Database username')
    parser.add_argument('--db-password', help='Database password')
    parser.add_argument('--db-name', help='Database name')
    parser.add_argument('--db-path', help='SQLite database file path (for SQLite only)')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.path):
        print(f"❌ Error: Path '{args.path}' does not exist")
        return
    
    print(f"🚀 Analyzing codebase at: {args.path}")
    
    if args.force_refresh:
        print("🔄 Force refresh enabled - ignoring cache")
    
    # Setup database config if provided
    db_config = None
    if args.db_type == 'sqlite':
        if args.db_path:
            db_config = {'database': args.db_path}
        elif args.db_name:
            db_config = {'database': args.db_name}
    else:
        if args.db_user and args.db_name:
            db_config = {
                'host': args.db_host,
                'user': args.db_user,
                'password': args.db_password or '',
                'database': args.db_name
            }
            
            # Set default ports
            if args.db_port:
                db_config['port'] = args.db_port
            elif args.db_type == 'mysql':
                db_config['port'] = 3306
            elif args.db_type == 'postgresql':
                db_config['port'] = 5432
                
            print(f"🗄️  Will also analyze {args.db_type.upper()} database: {args.db_name}")
    
    # Check database availability
    if db_config:
        if args.db_type == 'mysql' and not MYSQL_AVAILABLE:
            print("⚠️  MySQL connector not installed. Run: pip install mysql-connector-python")
            db_config = None
        elif args.db_type == 'postgresql' and not POSTGRES_AVAILABLE:
            print("⚠️  PostgreSQL connector not installed. Run: pip install psycopg2-binary")
            db_config = None
    
    indexer = CodebaseIndexer(args.path, db_config, args.db_type, args.cache_file, args.force_refresh)
    
    # Add custom ignore patterns
    if args.ignore:
        for pattern in args.ignore:
            indexer.add_ignore_pattern(pattern)
    
    # Scan and analyze
    indexer.scan_directory()
    
    if args.summary:
        indexer.print_summary()
    
    # Save the index
    indexer.save_index(args.output)
    
    print(f"\n✅ Done! Feed this index to your AI for efficient codebase understanding.")
    print(f"💡 Pro tip: Use this with AI prompts like 'Based on this codebase index...'")
    
    if not TREE_SITTER_AVAILABLE:
        print(f"\n🚀 Want better parsing? Install: pip install tree-sitter tree-sitter-languages")


if __name__ == '__main__':
    main()
) for arg in match.group(2).split(',') if arg.strip()]
                
                functions.append(FunctionInfo(
                    name=match.group(1),
                    args=args,
                    line_number=line_number
                ))
            
            # Extract PHP classes
            php_classes = []
            class_pattern = r'class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([^{]+))?\s*{'
            class_matches = re.finditer(class_pattern, content)
            
            for match in class_matches:
                class_name = match.group(1)
                extends = match.group(2)
                implements = [i.strip() for i in match.group(3).split(',')] if match.group(3) else []
                line_number = content[:match.start()].count('\n') + 1
                
                # Extract class methods and properties
                class_body = self._extract_php_class_body(content, match.end())
                methods = self._extract_php_methods(class_body)
                properties = self._extract_php_properties(class_body)
                
                php_classes.append(PHPClassInfo(
                    name=class_name,
                    namespace=namespace,
                    methods=methods,
                    properties=properties,
                    extends=extends,
                    implements=implements,
                    line_number=line_number
                ))
            
            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language='php',
                imports=imports,
                functions=functions,
                classes=[],  # Keep empty, use php_classes instead
                variables=[],
                php_classes=php_classes,
                namespace=namespace,
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing PHP file {file_path}: {e}")
            return self._create_basic_file_info(file_path, 'php')

    def _extract_php_class_body(self, content: str, start_pos: int) -> str:
        """Extract the body of a PHP class."""
        brace_count = 0
        body_start = start_pos
        
        for i, char in enumerate(content[start_pos:], start_pos):
            if char == '{':
                brace_count += 1
                if brace_count == 1:
                    body_start = i + 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    return content[body_start:i]
        
        return content[body_start:]

    def _extract_php_methods(self, class_body: str) -> List[FunctionInfo]:
        """Extract methods from PHP class body."""
        methods = []
        method_pattern = r'(?:public|private|protected)?\s*function\s+(\w+)\s*\(([^)]*)\)'
        
        for match in re.finditer(method_pattern, class_body):
            args = [arg.strip().split()[-1].lstrip('
        """Basic analysis of JavaScript/TypeScript files using regex."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract imports/requires
            imports = []
            import_patterns = [
                r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
                r'import\s+[\'"]([^\'"]+)[\'"]',
                r'require\([\'"]([^\'"]+)[\'"]\)',
                r'from\s+[\'"]([^\'"]+)[\'"]\s+import'
            ]
            
            for pattern in import_patterns:
                imports.extend(re.findall(pattern, content))
            
            # Extract function declarations
            functions = []
            func_patterns = [
                r'function\s+(\w+)\s*\(',
                r'const\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'let\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'var\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'(\w+)\s*:\s*(?:async\s+)?function',
                r'async\s+function\s+(\w+)\s*\('
            ]
            
            for pattern in func_patterns:
                functions.extend(re.findall(pattern, content))
            
            # Extract class declarations
            classes = []
            class_matches = re.findall(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content)
            
            lang = self.get_language(file_path)
            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language=lang,
                imports=list(set(imports)),
                functions=[FunctionInfo(name=f, args=[]) for f in set(functions)],
                classes=[ClassInfo(name=c[0], methods=[], bases=[c[1]] if c[1] else []) for c in classes],
                variables=[],
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return self._create_basic_file_info(file_path, self.get_language(file_path))

    def _create_basic_file_info(self, file_path: Path, language: str) -> FileInfo:
        """Create minimal file info when parsing fails."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                loc = len(f.readlines())
        except:
            loc = 0
            
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=language,
            imports=[],
            functions=[],
            classes=[],
            variables=[],
            loc=loc
        )

    def scan_directory(self) -> None:
        """Scan the directory and analyze all supported files."""
        for file_path in self.root_path.rglob('*'):
            if file_path.is_file() and not self.should_ignore(file_path):
                language = self.get_language(file_path)
                
                if language:
                    if language == 'python':
                        file_info = self.analyze_python_file(file_path)
                    elif language in ['javascript', 'typescript', 'jsx', 'tsx']:
                        file_info = self.analyze_javascript_file(file_path)
                    else:
                        file_info = self._create_basic_file_info(file_path, language)
                    
                    self.files.append(file_info)

    def generate_dependency_map(self) -> Dict[str, List[str]]:
        """Generate a dependency map showing which files import from which."""
        dep_map = {}
        
        for file_info in self.files:
            deps = []
            for imp in file_info.imports:
                # Try to resolve relative imports to actual files
                resolved = self._resolve_import(imp, file_info.path)
                if resolved:
                    deps.append(resolved)
                else:
                    deps.append(imp)  # External dependency
            dep_map[file_info.path] = deps
            
        return dep_map

    def _resolve_import(self, import_name: str, current_file: str) -> Optional[str]:
        """Try to resolve imports to actual files in the codebase."""
        # This is simplified - you might want to enhance this for your specific needs
        for file_info in self.files:
            file_name = Path(file_info.path).stem
            if import_name.endswith(file_name) or file_name in import_name:
                return file_info.path
        return None

    def create_summary(self) -> Dict[str, Any]:
        """Create the AI-friendly summary."""
        # Get overview stats
        total_files = len(self.files)
        total_loc = sum(f.loc for f in self.files)
        languages = set(f.language for f in self.files)
        
        # Count by language
        lang_stats = {}
        for lang in languages:
            files = [f for f in self.files if f.language == lang]
            lang_stats[lang] = {
                'files': len(files),
                'loc': sum(f.loc for f in files)
            }
        
        # Get all unique external dependencies
        external_deps = set()
        for file_info in self.files:
            for imp in file_info.imports:
                if not any(imp in f.path for f in self.files):
                    # Clean up the import name
                    clean_imp = imp.split('.')[0] if '.' in imp else imp
                    external_deps.add(clean_imp)
        
        # Create file summaries (the meat of the index)
        file_summaries = []
        for file_info in self.files:
            summary = {
                'path': file_info.path,
                'language': file_info.language,
                'loc': file_info.loc
            }
            
            if file_info.docstring:
                summary['description'] = file_info.docstring[:200]  # Truncate for token efficiency
            
            if file_info.imports:
                summary['imports'] = file_info.imports[:10]  # Limit for token efficiency
            
            if file_info.functions:
                summary['functions'] = [
                    {
                        'name': f.name,
                        'args': f.args,
                        'doc': f.docstring[:100] if f.docstring else None
                    } for f in file_info.functions[:20]  # Limit functions shown
                ]
            
            if file_info.classes:
                summary['classes'] = [
                    {
                        'name': c.name,
                        'bases': c.bases,
                        'methods': [m.name for m in c.methods[:15]],  # Just method names
                        'doc': c.docstring[:100] if c.docstring else None
                    } for c in file_info.classes[:10]  # Limit classes shown
                ]
            
            if file_info.variables:
                summary['key_variables'] = file_info.variables[:10]  # Top-level vars only
                
            file_summaries.append(summary)
        
        return {
            'project_overview': {
                'total_files': total_files,
                'total_loc': total_loc,
                'languages': lang_stats,
                'external_dependencies': sorted(list(external_deps))
            },
            'dependency_map': self.generate_dependency_map(),
            'files': file_summaries
        }

    def save_index(self, output_path: str = 'codebase_index.json') -> None:
        """Save the index to a JSON file."""
        summary = self.create_summary()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        
        print(f"📚 Codebase index saved to {output_path}")
        print(f"📊 Analyzed {len(self.files)} files ({sum(f.loc for f in self.files):,} lines)")
        print(f"🎯 Index size: {os.path.getsize(output_path) / 1024:.1f}KB")

    def add_ignore_pattern(self, pattern: str) -> None:
        """Add a custom ignore pattern."""
        self.ignore_patterns.add(pattern)

    def remove_ignore_pattern(self, pattern: str) -> None:
        """Remove an ignore pattern."""
        self.ignore_patterns.discard(pattern)

    def print_summary(self) -> None:
        """Print a quick summary to console."""
        summary = self.create_summary()
        overview = summary['project_overview']
        
        print("\n🔍 CODEBASE ANALYSIS SUMMARY")
        print("=" * 40)
        print(f"Total Files: {overview['total_files']}")
        print(f"Total Lines: {overview['total_loc']:,}")
        print(f"\nLanguage Breakdown:")
        for lang, stats in overview['languages'].items():
            print(f"  {lang}: {stats['files']} files ({stats['loc']:,} lines)")
        
        print(f"\nKey Dependencies:")
        deps = overview['external_dependencies'][:10]  # Show top 10
        for dep in deps:
            print(f"  • {dep}")
        
        if len(overview['external_dependencies']) > 10:
            print(f"  ... and {len(overview['external_dependencies']) - 10} more")


def main():
    parser = argparse.ArgumentParser(description='Analyze codebase and create AI-friendly index')
    parser.add_argument('path', help='Path to codebase root directory')
    parser.add_argument('-o', '--output', default='codebase_index.json', 
                       help='Output file name (default: codebase_index.json)')
    parser.add_argument('--ignore', action='append', 
                       help='Additional patterns to ignore (can be used multiple times)')
    parser.add_argument('--summary', action='store_true', 
                       help='Print summary to console')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.path):
        print(f"❌ Error: Path '{args.path}' does not exist")
        return
    
    print(f"🚀 Analyzing codebase at: {args.path}")
    
    indexer = CodebaseIndexer(args.path)
    
    # Add custom ignore patterns
    if args.ignore:
        for pattern in args.ignore:
            indexer.add_ignore_pattern(pattern)
    
    # Scan and analyze
    indexer.scan_directory()
    
    if args.summary:
        indexer.print_summary()
    
    # Save the index
    indexer.save_index(args.output)
    
    print(f"\n✅ Done! Feed this index to your AI for efficient codebase understanding.")


if __name__ == '__main__':
    main()
) for arg in match.group(2).split(',') if arg.strip()]
            methods.append(FunctionInfo(
                name=match.group(1),
                args=args,
                line_number=class_body[:match.start()].count('\n') + 1
            ))
        
        return methods

    def _extract_php_properties(self, class_body: str) -> List[str]:
        """Extract properties from PHP class body."""
        prop_pattern = r'(?:public|private|protected)\s+\$(\w+)'
        return re.findall(prop_pattern, class_body)

    def _extract_js_imports(self, content: str) -> List[str]:
        """Enhanced JavaScript/React import extraction."""
        imports = []
        
        # ES6 imports
        import_patterns = [
            r'import\s+\{([^}]+)\}\s+from\s+[\'"]([^\'"]+)[\'"]',  # Named imports
            r'import\s+(\w+)\s+from\s+[\'"]([^\'"]+)[\'"]',        # Default imports
            r'import\s+[\'"]([^\'"]+)[\'"]',                       # Side effect imports
            r'import\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)',            # Dynamic imports
        ]
        
        for pattern in import_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                if isinstance(match, tuple):
                    imports.append(match[-1])  # Get the module path
                else:
                    imports.append(match)
        
        # CommonJS requires
        require_matches = re.findall(r'require\s*\(\s*[\'"]([^\'"]+)[\'"]\s*\)', content)
        imports.extend(require_matches)
        
        return list(set(imports))

    def _extract_js_functions(self, content: str, exclude_names: List[str] = None) -> List[FunctionInfo]:
        """Extract JavaScript functions (excluding React components)."""
        exclude_names = exclude_names or []
        functions = []
        
        func_patterns = [
            r'function\s+(\w+)\s*\(([^)]*)\)',
            r'const\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>',
            r'let\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>',
            r'var\s+(\w+)\s*=\s*(?:async\s+)?\(([^)]*)\)\s*=>',
        ]
        
        for pattern in func_patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                func_name = match.group(1)
                if func_name not in exclude_names and not func_name[0].isupper():
                    args = [arg.strip() for arg in match.group(2).split(',') if arg.strip()]
                    functions.append(FunctionInfo(
                        name=func_name,
                        args=args,
                        line_number=content[:match.start()].count('\n') + 1
                    ))
        
        return functions

    def _extract_js_variables(self, content: str) -> List[str]:
        """Extract top-level JavaScript variables."""
        var_patterns = [
            r'const\s+(\w+)\s*=',
            r'let\s+(\w+)\s*=',
            r'var\s+(\w+)\s*='
        ]
        
        variables = []
        for pattern in var_patterns:
            variables.extend(re.findall(pattern, content))
        
        return list(set(variables))

    def analyze_database_schema(self) -> None:
        """Analyze database schema based on database type."""
        if not self.db_config:
            return
            
        try:
            if self.db_type == 'mysql':
                self._analyze_mysql_schema()
            elif self.db_type == 'postgresql':
                self._analyze_postgresql_schema()
            elif self.db_type == 'sqlite':
                self._analyze_sqlite_schema()
            else:
                print(f"❌ Unsupported database type: {self.db_type}")
        except Exception as e:
            print(f"❌ Database analysis failed: {e}")

    def _analyze_mysql_schema(self) -> None:
        """Analyze MySQL database schema."""
        if not MYSQL_AVAILABLE:
            print("❌ MySQL connector not available. Install: pip install mysql-connector-python")
            return
            
        conn = mysql.connector.connect(**self.db_config)
        cursor = conn.cursor(dictionary=True)
        
        # Get all tables
        cursor.execute("SHOW TABLES")
        tables = [list(row.values())[0] for row in cursor.fetchall()]
        
        for table_name in tables:
            # Get column information
            cursor.execute(f"DESCRIBE {table_name}")
            columns = []
            for col in cursor.fetchall():
                columns.append(DatabaseColumn(
                    name=col['Field'],
                    type=col['Type'],
                    nullable=col['Null'] == 'YES',
                    default=col['Default'],
                    key=col['Key'] if col['Key'] else None
                ))
            
            # Get foreign keys
            cursor.execute(f"""
                SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                WHERE TABLE_NAME = '{table_name}' AND REFERENCED_TABLE_NAME IS NOT NULL
            """)
            foreign_keys = [
                {
                    'column': fk['COLUMN_NAME'],
                    'references_table': fk['REFERENCED_TABLE_NAME'],
                    'references_column': fk['REFERENCED_COLUMN_NAME']
                }
                for fk in cursor.fetchall()
            ]
            
            # Get indexes
            cursor.execute(f"SHOW INDEX FROM {table_name}")
            indexes = list(set([idx['Key_name'] for idx in cursor.fetchall() if idx['Key_name'] != 'PRIMARY']))
            
            # Get sample data
            sample_data = self._get_sample_data(cursor, table_name)
            
            self.database_schema.append(DatabaseTable(
                name=table_name,
                columns=columns,
                foreign_keys=foreign_keys,
                indexes=indexes,
                sample_data=sample_data
            ))
        
        cursor.close()
        conn.close()

    def _analyze_postgresql_schema(self) -> None:
        """Analyze PostgreSQL database schema."""
        if not POSTGRES_AVAILABLE:
            print("❌ PostgreSQL connector not available. Install: pip install psycopg2-binary")
            return
            
        conn = psycopg2.connect(**self.db_config)
        cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        
        # Get all tables in public schema
        cursor.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """)
        tables = [row['table_name'] for row in cursor.fetchall()]
        
        for table_name in tables:
            # Get column information
            cursor.execute("""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = %s AND table_schema = 'public'
                ORDER BY ordinal_position
            """, (table_name,))
            
            columns = []
            for col in cursor.fetchall():
                columns.append(DatabaseColumn(
                    name=col['column_name'],
                    type=col['data_type'],
                    nullable=col['is_nullable'] == 'YES',
                    default=col['column_default']
                ))
            
            # Get foreign keys
            cursor.execute("""
                SELECT kcu.column_name, ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage AS ccu ON ccu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = %s
            """, (table_name,))
            
            foreign_keys = [
                {
                    'column': fk['column_name'],
                    'references_table': fk['foreign_table_name'],
                    'references_column': fk['foreign_column_name']
                }
                for fk in cursor.fetchall()
            ]
            
            # Get indexes
            cursor.execute("""
                SELECT indexname FROM pg_indexes 
                WHERE tablename = %s AND indexname NOT LIKE '%%_pkey'
            """, (table_name,))
            indexes = [row['indexname'] for row in cursor.fetchall()]
            
            # Get sample data
            sample_data = self._get_sample_data(cursor, table_name, is_postgres=True)
            
            self.database_schema.append(DatabaseTable(
                name=table_name,
                columns=columns,
                foreign_keys=foreign_keys,
                indexes=indexes,
                sample_data=sample_data
            ))
        
        cursor.close()
        conn.close()

    def _analyze_sqlite_schema(self) -> None:
        """Analyze SQLite database schema."""
        db_path = self.db_config.get('database') or self.db_config.get('path')
        if not db_path or not os.path.exists(db_path):
            print(f"❌ SQLite database not found: {db_path}")
            return
            
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # Enable dictionary-like access
        cursor = conn.cursor()
        
        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        tables = [row['name'] for row in cursor.fetchall()]
        
        for table_name in tables:
            # Get column information
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = []
            for col in cursor.fetchall():
                columns.append(DatabaseColumn(
                    name=col['name'],
                    type=col['type'],
                    nullable=not col['notnull'],
                    default=col['dflt_value'],
                    key='PRI' if col['pk'] else None
                ))
            
            # Get foreign keys
            cursor.execute(f"PRAGMA foreign_key_list({table_name})")
            foreign_keys = [
                {
                    'column': fk['from'],
                    'references_table': fk['table'],
                    'references_column': fk['to']
                }
                for fk in cursor.fetchall()
            ]
            
            # Get indexes
            cursor.execute(f"PRAGMA index_list({table_name})")
            indexes = [idx['name'] for idx in cursor.fetchall() if not idx['name'].startswith('sqlite_')]
            
            # Get sample data
            sample_data = self._get_sample_data(cursor, table_name, is_sqlite=True)
            
            self.database_schema.append(DatabaseTable(
                name=table_name,
                columns=columns,
                foreign_keys=foreign_keys,
                indexes=indexes,
                sample_data=sample_data
            ))
        
        cursor.close()
        conn.close()

    def _get_sample_data(self, cursor, table_name: str, is_postgres: bool = False, is_sqlite: bool = False) -> List[Dict]:
        """Get sample data from a table (database agnostic)."""
        try:
            if is_postgres:
                cursor.execute(f'SELECT * FROM "{table_name}" LIMIT 3')
            else:
                cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
            
            rows = cursor.fetchall()
            
            if is_sqlite:
                return [dict(row) for row in rows]
            else:
                return [dict(row) for row in rows]
                
        except Exception as e:
            print(f"⚠️  Could not fetch sample data from {table_name}: {e}")
            return []
        """Basic analysis of JavaScript/TypeScript files using regex."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Extract imports/requires
            imports = []
            import_patterns = [
                r'import\s+.*?\s+from\s+[\'"]([^\'"]+)[\'"]',
                r'import\s+[\'"]([^\'"]+)[\'"]',
                r'require\([\'"]([^\'"]+)[\'"]\)',
                r'from\s+[\'"]([^\'"]+)[\'"]\s+import'
            ]
            
            for pattern in import_patterns:
                imports.extend(re.findall(pattern, content))
            
            # Extract function declarations
            functions = []
            func_patterns = [
                r'function\s+(\w+)\s*\(',
                r'const\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'let\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'var\s+(\w+)\s*=\s*(?:async\s+)?\(',
                r'(\w+)\s*:\s*(?:async\s+)?function',
                r'async\s+function\s+(\w+)\s*\('
            ]
            
            for pattern in func_patterns:
                functions.extend(re.findall(pattern, content))
            
            # Extract class declarations
            classes = []
            class_matches = re.findall(r'class\s+(\w+)(?:\s+extends\s+(\w+))?', content)
            
            lang = self.get_language(file_path)
            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language=lang,
                imports=list(set(imports)),
                functions=[FunctionInfo(name=f, args=[]) for f in set(functions)],
                classes=[ClassInfo(name=c[0], methods=[], bases=[c[1]] if c[1] else []) for c in classes],
                variables=[],
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return self._create_basic_file_info(file_path, self.get_language(file_path))

    def _create_basic_file_info(self, file_path: Path, language: str) -> FileInfo:
        """Create minimal file info when parsing fails."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                loc = len(f.readlines())
        except:
            loc = 0
            
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=language,
            imports=[],
            functions=[],
            classes=[],
            variables=[],
            loc=loc
        )

    def scan_directory(self) -> None:
        """Scan the directory and analyze all supported files."""
        for file_path in self.root_path.rglob('*'):
            if file_path.is_file() and not self.should_ignore(file_path):
                language = self.get_language(file_path)
                
                if language:
                    if language == 'python':
                        file_info = self.analyze_python_file(file_path)
                    elif language in ['javascript', 'typescript', 'jsx', 'tsx']:
                        file_info = self.analyze_javascript_file(file_path)
                    else:
                        file_info = self._create_basic_file_info(file_path, language)
                    
                    self.files.append(file_info)

    def generate_dependency_map(self) -> Dict[str, List[str]]:
        """Generate a dependency map showing which files import from which."""
        dep_map = {}
        
        for file_info in self.files:
            deps = []
            for imp in file_info.imports:
                # Try to resolve relative imports to actual files
                resolved = self._resolve_import(imp, file_info.path)
                if resolved:
                    deps.append(resolved)
                else:
                    deps.append(imp)  # External dependency
            dep_map[file_info.path] = deps
            
        return dep_map

    def _resolve_import(self, import_name: str, current_file: str) -> Optional[str]:
        """Try to resolve imports to actual files in the codebase."""
        # This is simplified - you might want to enhance this for your specific needs
        for file_info in self.files:
            file_name = Path(file_info.path).stem
            if import_name.endswith(file_name) or file_name in import_name:
                return file_info.path
        return None

    def create_summary(self) -> Dict[str, Any]:
        """Create the AI-friendly summary."""
        # Get overview stats
        total_files = len(self.files)
        total_loc = sum(f.loc for f in self.files)
        languages = set(f.language for f in self.files)
        
        # Count by language
        lang_stats = {}
        for lang in languages:
            files = [f for f in self.files if f.language == lang]
            lang_stats[lang] = {
                'files': len(files),
                'loc': sum(f.loc for f in files)
            }
        
        # Get all unique external dependencies
        external_deps = set()
        for file_info in self.files:
            for imp in file_info.imports:
                if not any(imp in f.path for f in self.files):
                    # Clean up the import name
                    clean_imp = imp.split('.')[0] if '.' in imp else imp
                    external_deps.add(clean_imp)
        
        # Create file summaries (the meat of the index)
        file_summaries = []
        for file_info in self.files:
            summary = {
                'path': file_info.path,
                'language': file_info.language,
                'loc': file_info.loc
            }
            
            if file_info.docstring:
                summary['description'] = file_info.docstring[:200]  # Truncate for token efficiency
            
            if file_info.imports:
                summary['imports'] = file_info.imports[:10]  # Limit for token efficiency
            
            if file_info.functions:
                summary['functions'] = [
                    {
                        'name': f.name,
                        'args': f.args,
                        'doc': f.docstring[:100] if f.docstring else None
                    } for f in file_info.functions[:20]  # Limit functions shown
                ]
            
            if file_info.classes:
                summary['classes'] = [
                    {
                        'name': c.name,
                        'bases': c.bases,
                        'methods': [m.name for m in c.methods[:15]],  # Just method names
                        'doc': c.docstring[:100] if c.docstring else None
                    } for c in file_info.classes[:10]  # Limit classes shown
                ]
            
            if file_info.variables:
                summary['key_variables'] = file_info.variables[:10]  # Top-level vars only
                
            file_summaries.append(summary)
        
        return {
            'project_overview': {
                'total_files': total_files,
                'total_loc': total_loc,
                'languages': lang_stats,
                'external_dependencies': sorted(list(external_deps))
            },
            'dependency_map': self.generate_dependency_map(),
            'files': file_summaries
        }

    def save_index(self, output_path: str = 'codebase_index.json') -> None:
        """Save the index to a JSON file."""
        summary = self.create_summary()
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, indent=2, default=str)
        
        print(f"📚 Codebase index saved to {output_path}")
        print(f"📊 Analyzed {len(self.files)} files ({sum(f.loc for f in self.files):,} lines)")
        print(f"🎯 Index size: {os.path.getsize(output_path) / 1024:.1f}KB")

    def add_ignore_pattern(self, pattern: str) -> None:
        """Add a custom ignore pattern."""
        self.ignore_patterns.add(pattern)

    def remove_ignore_pattern(self, pattern: str) -> None:
        """Remove an ignore pattern."""
        self.ignore_patterns.discard(pattern)

    def print_summary(self) -> None:
        """Print a quick summary to console."""
        summary = self.create_summary()
        overview = summary['project_overview']
        
        print("\n🔍 CODEBASE ANALYSIS SUMMARY")
        print("=" * 40)
        print(f"Total Files: {overview['total_files']}")
        print(f"Total Lines: {overview['total_loc']:,}")
        print(f"\nLanguage Breakdown:")
        for lang, stats in overview['languages'].items():
            print(f"  {lang}: {stats['files']} files ({stats['loc']:,} lines)")
        
        print(f"\nKey Dependencies:")
        deps = overview['external_dependencies'][:10]  # Show top 10
        for dep in deps:
            print(f"  • {dep}")
        
        if len(overview['external_dependencies']) > 10:
            print(f"  ... and {len(overview['external_dependencies']) - 10} more")


def main():
    parser = argparse.ArgumentParser(description='Analyze codebase and create AI-friendly index')
    parser.add_argument('path', help='Path to codebase root directory')
    parser.add_argument('-o', '--output', default='codebase_index.json', 
                       help='Output file name (default: codebase_index.json)')
    parser.add_argument('--ignore', action='append', 
                       help='Additional patterns to ignore (can be used multiple times)')
    parser.add_argument('--summary', action='store_true', 
                       help='Print summary to console')
    
    args = parser.parse_args()
    
    if not os.path.exists(args.path):
        print(f"❌ Error: Path '{args.path}' does not exist")
        return
    
    print(f"🚀 Analyzing codebase at: {args.path}")
    
    indexer = CodebaseIndexer(args.path)
    
    # Add custom ignore patterns
    if args.ignore:
        for pattern in args.ignore:
            indexer.add_ignore_pattern(pattern)
    
    # Scan and analyze
    indexer.scan_directory()
    
    if args.summary:
        indexer.print_summary()
    
    # Save the index
    indexer.save_index(args.output)
    
    print(f"\n✅ Done! Feed this index to your AI for efficient codebase understanding.")


if __name__ == '__main__':
    main()