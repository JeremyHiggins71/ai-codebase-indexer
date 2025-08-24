#!/usr/bin/env python3
"""
Codebase Indexer - Creates AI-friendly summaries of codebases
Analyzes code structure and dependencies without wasting tokens on implementation details.
Supports React, PHP, C/C++, and MySQL schema analysis with smart vendor filtering.
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
    import tree_sitter_c
    import tree_sitter_cpp
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
    return_type: Optional[str] = None  # For TypeScript/PHP/C/C++

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
class CStructInfo:
    name: str
    members: List[str]
    is_typedef: bool = False
    line_number: int = 0

@dataclass
class CppClassInfo:
    name: str
    methods: List[FunctionInfo]
    members: List[str]
    bases: List[str]
    access_modifiers: Dict[str, str] = None  # method -> public/private/protected
    is_template: bool = False
    template_params: List[str] = None
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
    # C/C++-specific
    includes: List[str] = None  # #include statements
    defines: List[str] = None   # #define macros
    structs: List[CStructInfo] = None  # C structs
    cpp_classes: List[CppClassInfo] = None  # C++ classes
    namespaces: List[str] = None  # C++ namespaces used
    # Metadata for caching
    metadata: FileMetadata = None
    
    def __post_init__(self):
        if self.components is None:
            self.components = []
        if self.php_classes is None:
            self.php_classes = []
        if self.includes is None:
            self.includes = []
        if self.defines is None:
            self.defines = []
        if self.structs is None:
            self.structs = []
        if self.cpp_classes is None:
            self.cpp_classes = []
        if self.namespaces is None:
            self.namespaces = []

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
            'public/assets', 'static/vendor', 'assets/vendor', 'lib', 'libs',
            'third-party', 'third_party', 'vendors', 'bower_components',
            # C/C++ specific
            'external', 'deps', 'dependencies', 'cmake', 'CMakeFiles',
            '.vs', 'Debug', 'Release', 'x64', 'Win32', 'out',
            
            # Files
            '*.pyc', '*.pyo', '*.pyd', '*.so', '*.dll', '*.dylib',
            '*.log', '*.tmp', '*.temp', '*.bak', '*.swp', '*.swo',
            '.DS_Store', 'Thumbs.db', '*.min.js', '*.min.css',
            'package-lock.json', 'yarn.lock', 'poetry.lock', 'composer.lock',
            # C/C++ specific
            '*.o', '*.obj', '*.a', '*.lib', '*.pdb', '*.ilk', '*.exp',
            '*.exe', '*.out', 'CMakeCache.txt', 'Makefile', '*.vcxproj',
            '*.vcxproj.filters', '*.vcxproj.user', '*.sln', '*.suo'
        }
        
        # Common JavaScript/CSS libraries to skip (these are well-known)
        self.library_patterns = {
            # CSS Frameworks
            'bootstrap', 'bulma', 'tailwind', 'foundation', 'materialize',
            'semantic-ui', 'ui-kit', 'ant-design', 'chakra-ui', 'material-ui',
            
            # JS Libraries  
            'jquery', 'lodash', 'underscore', 'moment', 'axios', 'fetch',
            'react-dom', 'vue', 'angular', 'backbone', 'ember', 'knockout',
            'handlebars', 'mustache', 'chart.js', 'chartist', 'd3',
            'three.js', 'babylonjs', 'pixi.js', 'fabric.js', 'konva',
            
            # Utility Libraries
            'typeahead', 'select2', 'chosen', 'dropzone', 'sortable',
            'datepicker', 'timepicker', 'slider', 'carousel', 'modal',
            'tooltip', 'popover', 'accordion', 'tabs', 'dropdown',
            
            # Common CDN libraries
            'popper', 'tether', 'perfect-scrollbar', 'swiper', 'owl-carousel',
            'fancybox', 'lightbox', 'magnific-popup', 'photoswipe',
            
            # C/C++ Libraries
            'imgui', 'glfw', 'glad', 'glew', 'stb_image', 'stb_truetype',
            'glm', 'eigen', 'bullet', 'box2d', 'chipmunk', 'reactphysics3d',
            'assimp', 'tinyobjloader', 'rapidjson', 'nlohmann', 'fmt',
            'spdlog', 'catch2', 'gtest', 'boost', 'poco', 'curl', 'openssl',
            'zlib', 'libpng', 'libjpeg', 'freetype', 'sdl2', 'sfml', 'allegro',
            
            # Development tools that might leak into builds
            'webpack', 'rollup', 'parcel', 'browserify', 'gulp', 'grunt',
            'babel', 'typescript', 'eslint', 'prettier', 'jest', 'mocha'
        }
        
        # Language mappings
        self.language_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.jsx': 'react',
            '.tsx': 'react_ts',
            '.php': 'php',
            '.java': 'java',
            '.c': 'c',
            '.cpp': 'cpp',
            '.cc': 'cpp',
            '.cxx': 'cpp',
            '.c++': 'cpp',
            '.h': 'c_header',
            '.hpp': 'cpp_header',
            '.hh': 'cpp_header',
            '.hxx': 'cpp_header',
            '.h++': 'cpp_header',
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
        
        # File size thresholds for vendor detection
        self.max_vendor_file_size = 500 * 1024  # 500KB - likely vendor code
        self.min_minified_ratio = 0.8  # If >80% of lines are very long, probably minified

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
            
            # C parser
            self.parsers['c'] = tree_sitter.Parser()
            self.parsers['c'].set_language(tree_sitter_c.language())
            
            # C++ parser
            self.parsers['cpp'] = tree_sitter.Parser()
            self.parsers['cpp'].set_language(tree_sitter_cpp.language())
            
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
        
        # Handle C/C++ structures
        if file_data.get('structs'):
            file_data['structs'] = [CStructInfo(**s) for s in file_data['structs']]
        
        if file_data.get('cpp_classes'):
            file_data['cpp_classes'] = [CppClassInfo(
                name=c['name'],
                methods=[FunctionInfo(**m) for m in c.get('methods', [])],
                members=c.get('members', []),
                bases=c.get('bases', []),
                access_modifiers=c.get('access_modifiers'),
                is_template=c.get('is_template', False),
                template_params=c.get('template_params'),
                line_number=c.get('line_number', 0)
            ) for c in file_data['cpp_classes']]
            
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

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored based on patterns and smart detection."""
        path_str = str(path)
        name = path.name.lower()
        
        # Check basic ignore patterns first
        for pattern in self.ignore_patterns:
            if pattern.startswith('*.'):
                if name.endswith(pattern[1:]):
                    return True
            elif pattern in path_str or path.name == pattern:
                return True
        
        # Smart vendor/library detection
        if self._is_vendor_file(path):
            return True
            
        if self._is_minified_file(path):
            return True
            
        if self._is_known_library(path):
            return True
            
        if self._is_oversized_file(path):
            return True
        
        return False

    def _is_vendor_file(self, path: Path) -> bool:
        """Detect vendor/third-party files by path patterns."""
        path_str = str(path).lower()
        vendor_indicators = [
            '/vendor/', '/vendors/', '/third-party/', '/third_party/',
            '/libs/', '/lib/', '/assets/js/', '/assets/css/',
            '/public/js/', '/public/css/', '/static/js/', '/static/css/',
            '/cdn/', '/external/', '/plugins/', '/addons/'
        ]
        
        return any(indicator in path_str for indicator in vendor_indicators)

    def _is_known_library(self, path: Path) -> bool:
        """Check if file name matches known libraries."""
        name = path.stem.lower()  # filename without extension
        
        # Check against known library patterns
        for lib_pattern in self.library_patterns:
            if lib_pattern in name:
                return True
        
        # Check for common version patterns (e.g., jquery-3.6.1.js)
        if re.match(r'.+[-._]\d+(\.\d+)*', name):
            base_name = re.sub(r'[-._]\d+(\.\d+)*.*$', '', name)
            if base_name in self.library_patterns:
                return True
        
        # Common library naming patterns
        library_suffixes = [
            '.bundle', '.vendor', '.lib', '.plugin', '.widget',
            '.polyfill', '.shim', '.compat', '.legacy'
        ]
        
        for suffix in library_suffixes:
            if name.endswith(suffix):
                return True
        
        return False

    def _is_minified_file(self, path: Path) -> bool:
        """Detect minified files by analyzing content structure."""
        if not path.suffix.lower() in ['.js', '.css']:
            return False
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                # Read first few lines to check for minification
                lines = []
                for i, line in enumerate(f):
                    if i >= 10:  # Check first 10 lines
                        break
                    lines.append(line.strip())
            
            if not lines:
                return False
            
            # Heuristics for minified detection
            total_lines = len(lines)
            long_lines = sum(1 for line in lines if len(line) > 200)
            
            # If most lines are very long, probably minified
            if total_lines > 0 and (long_lines / total_lines) > self.min_minified_ratio:
                return True
            
            # Check for common minification patterns
            content_sample = ''.join(lines[:3])
            minified_indicators = [
                # No spaces around operators
                len(re.findall(r'\w+[=+\-*/]{1,2}\w+', content_sample)) > 10,
                # Very long single lines
                any(len(line) > 500 for line in lines[:3]),
                # Lack of proper spacing
                ';var ' in content_sample or ';function' in content_sample,
                # Compressed variable names pattern
                len(re.findall(r'\b[a-z]\b', content_sample)) > 20
            ]
            
            return sum(minified_indicators) >= 2
            
        except:
            return False

    def _is_oversized_file(self, path: Path) -> bool:
        """Skip files that are suspiciously large (likely vendor code)."""
        try:
            if path.stat().st_size > self.max_vendor_file_size:
                print(f"⏭️  Skipping large file (likely vendor): {path.name} ({path.stat().st_size / 1024:.0f}KB)")
                return True
        except:
            pass
        return False

    def _contains_vendor_markers(self, content: str) -> bool:
        """Check file content for vendor/library markers."""
        vendor_markers = [
            '* @license', '* @copyright', '* @author',
            'DO NOT EDIT', 'GENERATED FILE', 'AUTO-GENERATED',
            'This file is part of', 'Licensed under',
            '(c) 2', 'Copyright (c)', '* jQuery', '* Bootstrap',
            'Distributed under', 'MIT License', 'Apache License',
            '@preserve', 'minified', 'compressed'
        ]
        
        # Check first 1KB for copyright/license info
        header = content[:1024].lower()
        return any(marker.lower() in header for marker in vendor_markers)

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

    def analyze_react_file(self, file_path: Path) -> FileInfo:
        """Analyze React/JSX files for components, hooks, and props."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Use tree-sitter if available, otherwise fall back to regex
            if TREE_SITTER_AVAILABLE and 'javascript' in self.parsers:
                return self._analyze_react_with_tree_sitter(file_path, content)
            else:
                return self._analyze_react_with_regex(file_path, content)
                
        except Exception as e:
            print(f"Error analyzing React file {file_path}: {e}")
            return self._create_basic_file_info(file_path, self.get_language(file_path))

    def _analyze_react_with_tree_sitter(self, file_path: Path, content: str) -> FileInfo:
        """Enhanced React analysis using tree-sitter."""
        tree = self.parsers['javascript'].parse(bytes(content, 'utf8'))
        root_node = tree.root_node
        
        imports = []
        components = []
        functions = []
        variables = []
        
        def traverse_node(node):
            if node.type == 'import_statement':
                # Extract import information
                import_text = content[node.start_byte:node.end_byte]
                import_match = re.search(r'from\s+[\'"]([^\'"]+)[\'"]', import_text)
                if import_match:
                    imports.append(import_match.group(1))
            
            elif node.type in ['function_declaration', 'arrow_function', 'function_expression']:
                func_info = self._extract_function_from_node(node, content)
                if func_info:
                    # Check if it's a React component (starts with uppercase)
                    if func_info.name and func_info.name[0].isupper():
                        # Analyze as React component
                        component = self._analyze_react_component_node(node, content, func_info.name)
                        if component:
                            components.append(component)
                    else:
                        functions.append(func_info)
            
            elif node.type == 'variable_declarator':
                var_name = content[node.children[0].start_byte:node.children[0].end_byte]
                variables.append(var_name)
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child)
        
        traverse_node(root_node)
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=list(set(imports)),
            functions=functions,
            classes=[],
            variables=list(set(variables)),
            components=components,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _analyze_react_with_regex(self, file_path: Path, content: str) -> FileInfo:
        """Fallback React analysis using regex (original method)."""
        # Extract imports
        imports = self._extract_js_imports(content)
        
        # Extract React components
        components = self._extract_react_components(content)
        
        # Extract regular functions (non-component)
        functions = self._extract_js_functions(content, [c.name for c in components])
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=imports,
            functions=functions,
            classes=[],  # React rarely uses classes now
            variables=self._extract_js_variables(content),
            components=components,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _extract_function_from_node(self, node, content: str) -> Optional[FunctionInfo]:
        """Extract function information from tree-sitter node."""
        try:
            func_name = None
            args = []
            
            # Find function name and parameters
            for child in node.children:
                if child.type == 'identifier':
                    func_name = content[child.start_byte:child.end_byte]
                elif child.type == 'formal_parameters':
                    # Extract parameter names
                    for param in child.children:
                        if param.type == 'identifier':
                            args.append(content[param.start_byte:param.end_byte])
            
            if func_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return FunctionInfo(name=func_name, args=args, line_number=line_number)
                
        except Exception:
            pass
        
        return None

    def _analyze_react_component_node(self, node, content: str, comp_name: str) -> Optional[ReactComponentInfo]:
        """Analyze a React component using tree-sitter."""
        try:
            # Extract props from function parameters
            props = []
            hooks = []
            
            # Look for destructured props in parameters
            for child in node.children:
                if child.type == 'formal_parameters':
                    param_text = content[child.start_byte:child.end_byte]
                    # Simple destructuring detection
                    if '{' in param_text and '}' in param_text:
                        prop_match = re.findall(r'(\w+)(?:\s*:\s*\w+)?', param_text)
                        props.extend([p for p in prop_match if p not in ['const', 'let', 'var']])
            
            # Extract hooks from function body
            func_body = content[node.start_byte:node.end_byte]
            hook_matches = re.findall(r'(use\w+)\s*\(', func_body)
            hooks.extend(hook_matches)
            
            # Determine export type
            exports = self._get_export_type(content, comp_name)
            
            line_number = content[:node.start_byte].count('\n') + 1
            
            return ReactComponentInfo(
                name=comp_name,
                props=list(set(props)),
                hooks=list(set(hooks)),
                exports=exports,
                line_number=line_number
            )
            
        except Exception:
            return None

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

    def _extract_component_props(self, content: str, comp_name: str, start_pos: int) -> List[str]:
        """Extract props from a React component."""
        props = []
        
        # Look for function parameters with destructuring
        func_match = re.search(rf'{re.escape(comp_name)}\s*=\s*\(\{{([^}}]+)\}}\)', content[start_pos:start_pos+200])
        if func_match:
            prop_string = func_match.group(1)
            props.extend([p.strip() for p in prop_string.split(',') if p.strip()])
        
        # Look for props.something usage
        component_body = self._extract_function_body(content, comp_name, start_pos)
        prop_usage = re.findall(r'props\.(\w+)', component_body)
        props.extend(prop_usage)
        
        return list(set(props))  # Remove duplicates

    def _extract_component_hooks(self, content: str, comp_name: str, start_pos: int) -> List[str]:
        """Extract React hooks from a component."""
        component_body = self._extract_function_body(content, comp_name, start_pos)
        
        # Common hooks patterns
        hook_patterns = [
            r'use(State|Effect|Context|Reducer|Callback|Memo|Ref|LayoutEffect)\s*\(',
            r'use\w+\s*\(',  # Custom hooks
        ]
        
        hooks = []
        for pattern in hook_patterns:
            matches = re.findall(pattern, component_body)
            hooks.extend([f"use{match}" if isinstance(match, str) and match else match for match in matches])
        
        return list(set(hooks))

    def _extract_function_body(self, content: str, func_name: str, start_pos: int) -> str:
        """Extract the body of a function for analysis."""
        # This is a simplified approach - could be enhanced with proper parsing
        lines = content[start_pos:].split('\n')
        body_lines = []
        brace_count = 0
        started = False
        
        for line in lines[:100]:  # Limit search to first 100 lines
            if '{' in line:
                started = True
                brace_count += line.count('{')
            if started:
                body_lines.append(line)
                brace_count -= line.count('}')
                if brace_count <= 0:
                    break
        
        return '\n'.join(body_lines)

    def _get_export_type(self, content: str, comp_name: str) -> str:
        """Determine how a component is exported."""
        if f'export default {comp_name}' in content or f'export default function {comp_name}' in content:
            return 'default'
        elif f'export {{ {comp_name} }}' in content or f'export function {comp_name}' in content:
            return 'named'
        elif 'export default' in content and f'export {{ {comp_name} }}' in content:
            return 'both'
        return 'none'

    def analyze_php_file(self, file_path: Path) -> FileInfo:
        """Analyze PHP files for classes, functions, and namespaces."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Use tree-sitter if available, otherwise fall back to regex
            if TREE_SITTER_AVAILABLE and 'php' in self.parsers:
                return self._analyze_php_with_tree_sitter(file_path, content)
            else:
                return self._analyze_php_with_regex(file_path, content)
                
        except Exception as e:
            print(f"Error analyzing PHP file {file_path}: {e}")
            return self._create_basic_file_info(file_path, 'php')

    def _analyze_php_with_tree_sitter(self, file_path: Path, content: str) -> FileInfo:
        """Enhanced PHP analysis using tree-sitter."""
        tree = self.parsers['php'].parse(bytes(content, 'utf8'))
        root_node = tree.root_node
        
        namespace = None
        imports = []
        functions = []
        php_classes = []
        
        def traverse_node(node):
            nonlocal namespace
            
            if node.type == 'namespace_definition':
                namespace_node = node.children[1] if len(node.children) > 1 else None
                if namespace_node:
                    namespace = content[namespace_node.start_byte:namespace_node.end_byte]
            
            elif node.type == 'use_declaration':
                use_text = content[node.start_byte:node.end_byte]
                use_match = re.search(r'use\s+([^;]+);', use_text)
                if use_match:
                    imports.append(use_match.group(1).strip())
            
            elif node.type == 'function_definition':
                func_info = self._extract_php_function_from_node(node, content)
                if func_info:
                    functions.append(func_info)
            
            elif node.type == 'class_declaration':
                class_info = self._extract_php_class_from_node(node, content, namespace)
                if class_info:
                    php_classes.append(class_info)
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child)
        
        traverse_node(root_node)
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language='php',
            imports=imports,
            functions=functions,
            classes=[],
            variables=[],
            php_classes=php_classes,
            namespace=namespace,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _analyze_php_with_regex(self, file_path: Path, content: str) -> FileInfo:
        """Fallback PHP analysis using regex (original method)."""
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
            args = [arg.strip().split()[-1].lstrip('$') for arg in match.group(2).split(',') if arg.strip()]
            
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
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language='php',
            imports=imports,
            functions=functions,
            classes=[],  # Keep empty, use php_classes instead
            variables=[],
            php_classes=php_classes,
            namespace=namespace,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _extract_php_function_from_node(self, node, content: str) -> Optional[FunctionInfo]:
        """Extract PHP function info from tree-sitter node."""
        try:
            func_name = None
            args = []
            return_type = None
            
            for child in node.children:
                if child.type == 'name' and not func_name:
                    func_name = content[child.start_byte:child.end_byte]
                elif child.type == 'formal_parameters':
                    # Extract parameter information
                    for param in child.children:
                        if param.type == 'simple_parameter':
                            param_name = content[param.start_byte:param.end_byte].lstrip('$')
                            args.append(param_name)
                elif child.type == 'primitive_type':
                    return_type = content[child.start_byte:child.end_byte]
            
            if func_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return FunctionInfo(
                    name=func_name, 
                    args=args, 
                    line_number=line_number,
                    return_type=return_type
                )
                
        except Exception:
            pass
        
        return None

    def _extract_php_class_from_node(self, node, content: str, namespace: Optional[str]) -> Optional[PHPClassInfo]:
        """Extract PHP class info from tree-sitter node."""
        try:
            class_name = None
            extends = None
            implements = []
            methods = []
            properties = []
            
            for child in node.children:
                if child.type == 'name' and not class_name:
                    class_name = content[child.start_byte:child.end_byte]
                elif child.type == 'base_clause':
                    extends = content[child.start_byte:child.end_byte].replace('extends', '').strip()
                elif child.type == 'class_interface_clause':
                    impl_text = content[child.start_byte:child.end_byte]
                    implements = [i.strip() for i in impl_text.replace('implements', '').split(',')]
                elif child.type == 'declaration_list':
                    # Extract methods and properties from class body
                    for member in child.children:
                        if member.type == 'method_declaration':
                            method_info = self._extract_php_function_from_node(member, content)
                            if method_info:
                                methods.append(method_info)
                        elif member.type == 'property_declaration':
                            prop_text = content[member.start_byte:member.end_byte]
                            prop_names = re.findall(r'\$(\w+)', prop_text)
                            properties.extend(prop_names)
            
            if class_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return PHPClassInfo(
                    name=class_name,
                    namespace=namespace,
                    methods=methods,
                    properties=properties,
                    extends=extends,
                    implements=implements,
                    line_number=line_number
                )
                
        except Exception:
            pass
        
        return None

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
            args = [arg.strip().split()[-1].lstrip('$') for arg in match.group(2).split(',') if arg.strip()]
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

    def analyze_c_file(self, file_path: Path) -> FileInfo:
        """Analyze C files for functions, structs, and includes."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Use tree-sitter if available, otherwise fall back to regex
            if TREE_SITTER_AVAILABLE and 'c' in self.parsers:
                return self._analyze_c_with_tree_sitter(file_path, content)
            else:
                return self._analyze_c_with_regex(file_path, content)
                
        except Exception as e:
            print(f"Error analyzing C file {file_path}: {e}")
            return self._create_basic_file_info(file_path, 'c')

    def analyze_cpp_file(self, file_path: Path) -> FileInfo:
        """Analyze C++ files for classes, functions, namespaces, and templates."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Use tree-sitter if available, otherwise fall back to regex
            if TREE_SITTER_AVAILABLE and 'cpp' in self.parsers:
                return self._analyze_cpp_with_tree_sitter(file_path, content)
            else:
                return self._analyze_cpp_with_regex(file_path, content)
                
        except Exception as e:
            print(f"Error analyzing C++ file {file_path}: {e}")
            return self._create_basic_file_info(file_path, 'cpp')

    def _analyze_c_with_tree_sitter(self, file_path: Path, content: str) -> FileInfo:
        """Enhanced C analysis using tree-sitter."""
        tree = self.parsers['c'].parse(bytes(content, 'utf8'))
        root_node = tree.root_node
        
        includes = []
        functions = []
        structs = []
        defines = []
        variables = []
        
        def traverse_node(node):
            if node.type == 'preproc_include':
                # Extract #include statements
                include_text = content[node.start_byte:node.end_byte]
                include_match = re.search(r'#include\s*[<"]([^>"]+)[>"]', include_text)
                if include_match:
                    includes.append(include_match.group(1))
            
            elif node.type == 'preproc_def':
                # Extract #define macros
                define_text = content[node.start_byte:node.end_byte]
                define_match = re.search(r'#define\s+(\w+)', define_text)
                if define_match:
                    defines.append(define_match.group(1))
            
            elif node.type == 'function_definition':
                func_info = self._extract_c_function_from_node(node, content)
                if func_info:
                    functions.append(func_info)
            
            elif node.type == 'struct_specifier':
                struct_info = self._extract_c_struct_from_node(node, content)
                if struct_info:
                    structs.append(struct_info)
            
            elif node.type == 'declaration':
                # Global variables
                var_names = self._extract_c_variables_from_node(node, content)
                variables.extend(var_names)
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child)
        
        traverse_node(root_node)
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=includes,  # Use includes for C
            functions=functions,
            classes=[],  # C doesn't have classes
            variables=variables,
            includes=includes,
            defines=defines,
            structs=structs,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _analyze_c_with_regex(self, file_path: Path, content: str) -> FileInfo:
        """Fallback C analysis using regex."""
        # Extract includes
        includes = re.findall(r'#include\s*[<"]([^>"]+)[>"]', content)
        
        # Extract defines
        defines = re.findall(r'#define\s+(\w+)', content)
        
        # Extract functions
        functions = []
        func_pattern = r'(?:^\s*(?:static\s+|extern\s+)?(?:const\s+)?(?:unsigned\s+)?(?:signed\s+)?)?' \
                      r'(\w+(?:\s*\*)*)\s+(\w+)\s*\(([^)]*)\)\s*{'
        
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            return_type = match.group(1).strip() if match.group(1) else 'void'
            func_name = match.group(2)
            params_str = match.group(3)
            
            # Parse parameters
            args = []
            if params_str.strip() and params_str.strip() != 'void':
                for param in params_str.split(','):
                    param = param.strip()
                    if param:
                        # Extract parameter name (last word)
                        param_parts = param.split()
                        if param_parts:
                            args.append(param_parts[-1].lstrip('*'))
            
            line_number = content[:match.start()].count('\n') + 1
            functions.append(FunctionInfo(
                name=func_name,
                args=args,
                return_type=return_type,
                line_number=line_number
            ))
        
        # Extract structs
        structs = []
        struct_pattern = r'(?:typedef\s+)?struct\s+(\w+)?\s*\{([^}]+)\}(?:\s*(\w+))?;'
        
        for match in re.finditer(struct_pattern, content, re.DOTALL):
            struct_name = match.group(1) or match.group(3)
            if not struct_name:
                continue
                
            struct_body = match.group(2)
            members = []
            
            # Extract struct members
            for line in struct_body.split('\n'):
                line = line.strip()
                if line and not line.startswith('//') and not line.startswith('/*'):
                    member_match = re.search(r'\w+\s+(\w+)\s*[;\[]', line)
                    if member_match:
                        members.append(member_match.group(1))
            
            line_number = content[:match.start()].count('\n') + 1
            is_typedef = match.group(0).startswith('typedef')
            
            structs.append(CStructInfo(
                name=struct_name,
                members=members,
                is_typedef=is_typedef,
                line_number=line_number
            ))
        
        # Extract global variables (simplified)
        variables = []
        var_pattern = r'(?:^|\n)\s*(?:static\s+|extern\s+)?(?:const\s+)?(?:unsigned\s+)?(?:signed\s+)?' \
                     r'\w+(?:\s*\*)*\s+(\w+)(?:\s*=\s*[^;]+)?;'
        
        for match in re.finditer(var_pattern, content):
            var_name = match.group(1)
            if var_name not in ['if', 'for', 'while', 'switch', 'return']:  # Avoid control structures
                variables.append(var_name)
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=includes,
            functions=functions,
            classes=[],
            variables=list(set(variables))[:10],  # Limit to prevent noise
            includes=includes,
            defines=defines,
            structs=structs,
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _analyze_cpp_with_tree_sitter(self, file_path: Path, content: str) -> FileInfo:
        """Enhanced C++ analysis using tree-sitter."""
        tree = self.parsers['cpp'].parse(bytes(content, 'utf8'))
        root_node = tree.root_node
        
        includes = []
        functions = []
        cpp_classes = []
        namespaces = []
        defines = []
        variables = []
        
        def traverse_node(node):
            if node.type == 'preproc_include':
                include_text = content[node.start_byte:node.end_byte]
                include_match = re.search(r'#include\s*[<"]([^>"]+)[>"]', include_text)
                if include_match:
                    includes.append(include_match.group(1))
            
            elif node.type == 'preproc_def':
                define_text = content[node.start_byte:node.end_byte]
                define_match = re.search(r'#define\s+(\w+)', define_text)
                if define_match:
                    defines.append(define_match.group(1))
            
            elif node.type == 'namespace_definition':
                namespace_name = self._extract_cpp_namespace_from_node(node, content)
                if namespace_name:
                    namespaces.append(namespace_name)
            
            elif node.type == 'class_specifier':
                class_info = self._extract_cpp_class_from_node(node, content)
                if class_info:
                    cpp_classes.append(class_info)
            
            elif node.type == 'function_definition':
                func_info = self._extract_cpp_function_from_node(node, content)
                if func_info:
                    functions.append(func_info)
            
            # Recursively traverse children
            for child in node.children:
                traverse_node(child)
        
        traverse_node(root_node)
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=includes,
            functions=functions,
            classes=[],  # Keep empty, use cpp_classes
            variables=variables,
            includes=includes,
            defines=defines,
            cpp_classes=cpp_classes,
            namespaces=list(set(namespaces)),
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _analyze_cpp_with_regex(self, file_path: Path, content: str) -> FileInfo:
        """Fallback C++ analysis using regex."""
        # Extract includes
        includes = re.findall(r'#include\s*[<"]([^>"]+)[>"]', content)
        
        # Extract defines
        defines = re.findall(r'#define\s+(\w+)', content)
        
        # Extract namespaces
        namespaces = re.findall(r'namespace\s+(\w+)', content)
        namespaces.extend(re.findall(r'using\s+namespace\s+(\w+)', content))
        
        # Extract functions (more complex for C++ with templates)
        functions = []
        func_pattern = r'(?:template\s*<[^>]*>)?\s*' \
                      r'(?:(?:inline|static|virtual|explicit|const|constexpr)\s+)*' \
                      r'(\w+(?:\s*[*&])*(?:\s*<[^>]*>)?)\s+' \
                      r'(\w+)\s*\(([^)]*)\)(?:\s*const)?\s*(?:override)?\s*(?:final)?\s*{'
        
        for match in re.finditer(func_pattern, content, re.MULTILINE):
            return_type = match.group(1).strip()
            func_name = match.group(2)
            params_str = match.group(3)
            
            # Parse parameters
            args = []
            if params_str.strip():
                for param in params_str.split(','):
                    param = param.strip()
                    if param and param != 'void':
                        param_parts = param.split()
                        if param_parts:
                            args.append(param_parts[-1].lstrip('*&'))
            
            line_number = content[:match.start()].count('\n') + 1
            functions.append(FunctionInfo(
                name=func_name,
                args=args,
                return_type=return_type,
                line_number=line_number
            ))
        
        # Extract classes
        cpp_classes = []
        class_pattern = r'(?:template\s*<[^>]*>)?\s*' \
                       r'class\s+(\w+)(?:\s*:\s*(?:public|private|protected)\s+(\w+(?:,\s*(?:public|private|protected)\s+\w+)*))?\s*{'
        
        for match in re.finditer(class_pattern, content, re.MULTILINE):
            class_name = match.group(1)
            inheritance = match.group(2) if match.group(2) else ""
            
            # Parse base classes
            bases = []
            if inheritance:
                for base in inheritance.split(','):
                    base_clean = re.sub(r'(?:public|private|protected)\s+', '', base.strip())
                    if base_clean:
                        bases.append(base_clean)
            
            # Extract methods from class body (simplified)
            class_start = match.end()
            brace_count = 1
            class_end = class_start
            
            for i, char in enumerate(content[class_start:], class_start):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        class_end = i
                        break
            
            class_body = content[class_start:class_end]
            methods = []
            
            # Extract methods from class body
            method_pattern = r'(?:(?:public|private|protected):\s*)?' \
                           r'(?:(?:virtual|static|inline|const|constexpr)\s+)*' \
                           r'(\w+(?:\s*[*&])*)\s+(\w+)\s*\([^)]*\)(?:\s*const)?(?:\s*override)?(?:\s*final)?'
            
            for method_match in re.finditer(method_pattern, class_body):
                method_name = method_match.group(2)
                # Skip obvious non-methods
                if method_name not in ['if', 'for', 'while', 'switch', 'class', 'struct']:
                    methods.append(FunctionInfo(
                        name=method_name,
                        args=[],  # Simplified for now
                        return_type=method_match.group(1),
                        line_number=content[:class_start + method_match.start()].count('\n') + 1
                    ))
            
            line_number = content[:match.start()].count('\n') + 1
            is_template = 'template' in match.group(0)
            
            cpp_classes.append(CppClassInfo(
                name=class_name,
                methods=methods[:10],  # Limit for token efficiency
                members=[],  # Could be extracted but complex
                bases=bases,
                is_template=is_template,
                line_number=line_number
            ))
        
        metadata = self._get_file_metadata(file_path)
        
        return FileInfo(
            path=str(file_path.relative_to(self.root_path)),
            language=self.get_language(file_path),
            imports=includes,
            functions=functions[:20],  # Limit for token efficiency
            classes=[],
            variables=[],
            includes=includes,
            defines=defines,
            cpp_classes=cpp_classes,
            namespaces=list(set(namespaces)),
            metadata=metadata,
            loc=len(content.splitlines())
        )

    def _extract_c_function_from_node(self, node, content: str) -> Optional[FunctionInfo]:
        """Extract C function info from tree-sitter node."""
        try:
            func_name = None
            args = []
            return_type = None
            
            for child in node.children:
                if child.type == 'function_declarator':
                    # Extract function name and parameters
                    for subchild in child.children:
                        if subchild.type == 'identifier':
                            func_name = content[subchild.start_byte:subchild.end_byte]
                        elif subchild.type == 'parameter_list':
                            for param in subchild.children:
                                if param.type == 'parameter_declaration':
                                    param_name = self._extract_c_param_name(param, content)
                                    if param_name:
                                        args.append(param_name)
                elif child.type in ['primitive_type', 'type_identifier']:
                    return_type = content[child.start_byte:child.end_byte]
            
            if func_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return FunctionInfo(
                    name=func_name,
                    args=args,
                    return_type=return_type,
                    line_number=line_number
                )
                
        except Exception:
            pass
        
        return None

    def _extract_c_struct_from_node(self, node, content: str) -> Optional[CStructInfo]:
        """Extract C struct info from tree-sitter node."""
        try:
            struct_name = None
            members = []
            
            for child in node.children:
                if child.type == 'type_identifier':
                    struct_name = content[child.start_byte:child.end_byte]
                elif child.type == 'field_declaration_list':
                    for field in child.children:
                        if field.type == 'field_declaration':
                            member_name = self._extract_c_field_name(field, content)
                            if member_name:
                                members.append(member_name)
            
            if struct_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return CStructInfo(
                    name=struct_name,
                    members=members,
                    line_number=line_number
                )
                
        except Exception:
            pass
        
        return None

    def _extract_c_variables_from_node(self, node, content: str) -> List[str]:
        """Extract variable names from C declaration node."""
        variables = []
        try:
            for child in node.children:
                if child.type == 'init_declarator':
                    for subchild in child.children:
                        if subchild.type == 'identifier':
                            var_name = content[subchild.start_byte:subchild.end_byte]
                            variables.append(var_name)
        except Exception:
            pass
        
        return variables

    def _extract_c_param_name(self, node, content: str) -> Optional[str]:
        """Extract parameter name from C parameter node."""
        try:
            for child in node.children:
                if child.type == 'identifier':
                    return content[child.start_byte:child.end_byte]
        except Exception:
            pass
        return None

    def _extract_c_field_name(self, node, content: str) -> Optional[str]:
        """Extract field name from C field declaration."""
        try:
            for child in node.children:
                if child.type == 'field_identifier':
                    return content[child.start_byte:child.end_byte]
        except Exception:
            pass
        return None

    def _extract_cpp_namespace_from_node(self, node, content: str) -> Optional[str]:
        """Extract namespace name from C++ namespace node."""
        try:
            for child in node.children:
                if child.type == 'identifier':
                    return content[child.start_byte:child.end_byte]
        except Exception:
            pass
        return None

    def _extract_cpp_class_from_node(self, node, content: str) -> Optional[CppClassInfo]:
        """Extract C++ class info from tree-sitter node."""
        try:
            class_name = None
            methods = []
            bases = []
            
            for child in node.children:
                if child.type == 'type_identifier':
                    class_name = content[child.start_byte:child.end_byte]
                elif child.type == 'base_class_clause':
                    # Extract base classes
                    for base_child in child.children:
                        if base_child.type == 'type_identifier':
                            base_name = content[base_child.start_byte:base_child.end_byte]
                            bases.append(base_name)
                elif child.type == 'field_declaration_list':
                    # Extract methods from class body
                    for method_node in child.children:
                        if method_node.type == 'function_definition':
                            method_info = self._extract_cpp_function_from_node(method_node, content)
                            if method_info:
                                methods.append(method_info)
            
            if class_name:
                line_number = content[:node.start_byte].count('\n') + 1
                return CppClassInfo(
                    name=class_name,
                    methods=methods,
                    members=[],  # Could extract but complex
                    bases=bases,
                    line_number=line_number
                )
                
        except Exception:
            pass
        
        return None

    def _extract_cpp_function_from_node(self, node, content: str) -> Optional[FunctionInfo]:
        """Extract C++ function info from tree-sitter node."""
        # Similar to C function extraction but handles C++ specifics
        return self._extract_c_function_from_node(node, content)

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

    def analyze_javascript_file(self, file_path: Path) -> FileInfo:
        """Basic analysis of JavaScript/TypeScript files using regex."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            imports = self._extract_js_imports(content)
            functions = self._extract_js_functions(content)
            variables = self._extract_js_variables(content)
            
            # Extract class declarations
            classes = []
            class_matches = re.finditer(r'class\s+(\w+)(?:\s+extends\s+(\w+))?\s*{', content)
            for match in class_matches:
                class_body = self._extract_function_body(content, match.group(1), match.start())
                methods = self._extract_js_class_methods(class_body)
                
                classes.append(ClassInfo(
                    name=match.group(1),
                    methods=methods,
                    bases=[match.group(2)] if match.group(2) else [],
                    line_number=content[:match.start()].count('\n') + 1
                ))
            
            metadata = self._get_file_metadata(file_path)
            lang = self.get_language(file_path)
            
            return FileInfo(
                path=str(file_path.relative_to(self.root_path)),
                language=lang,
                imports=imports,
                functions=functions,
                classes=classes,
                variables=variables,
                metadata=metadata,
                loc=len(content.splitlines())
            )
            
        except Exception as e:
            print(f"Error analyzing {file_path}: {e}")
            return self._create_basic_file_info(file_path, self.get_language(file_path))

    def _extract_js_class_methods(self, class_body: str) -> List[FunctionInfo]:
        """Extract methods from JavaScript class body."""
        methods = []
        method_pattern = r'(?:async\s+)?(\w+)\s*\(([^)]*)\)\s*{'
        
        for match in re.finditer(method_pattern, class_body):
            if match.group(1) not in ['if', 'for', 'while', 'switch']:  # Avoid control structures
                args = [arg.strip() for arg in match.group(2).split(',') if arg.strip()]
                methods.append(FunctionInfo(
                    name=match.group(1),
                    args=args,
                    line_number=class_body[:match.start()].count('\n') + 1
                ))
        
        return methods

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
            includes=[],
            defines=[],
            structs=[],
            cpp_classes=[],
            namespaces=[],
            metadata=metadata,
            loc=loc
        )

    def scan_directory(self) -> None:
        """Scan the directory and analyze all supported files."""
        print("🔍 Scanning codebase...")
        
        analyzed_count = 0
        cached_count = 0
        skipped_vendor = 0
        skipped_minified = 0
        skipped_oversized = 0
        
        for file_path in self.root_path.rglob('*'):
            if file_path.is_file():
                # Check different types of ignoring for stats
                if self._is_basic_ignore(file_path):
                    continue
                elif self._is_vendor_file(file_path):
                    skipped_vendor += 1
                    continue
                elif self._is_minified_file(file_path):
                    skipped_minified += 1
                    continue
                elif self._is_known_library(file_path):
                    skipped_vendor += 1
                    continue
                elif self._is_oversized_file(file_path):
                    skipped_oversized += 1
                    continue
                
                language = self.get_language(file_path)
                if language:
                    # Check if we need to re-analyze this file
                    if self._should_reanalyze_file(file_path):
                        # Additional content-based vendor detection
                        if self._is_vendor_by_content(file_path):
                            skipped_vendor += 1
                            continue
                            
                        if language == 'python':
                            file_info = self.analyze_python_file(file_path)
                        elif language in ['react', 'react_ts']:
                            file_info = self.analyze_react_file(file_path)
                        elif language in ['javascript', 'typescript']:
                            file_info = self.analyze_javascript_file(file_path)
                        elif language == 'php':
                            file_info = self.analyze_php_file(file_path)
                        elif language in ['c', 'c_header']:
                            file_info = self.analyze_c_file(file_path)
                        elif language in ['cpp', 'cpp_header']:
                            file_info = self.analyze_cpp_file(file_path)
                        else:
                            file_info = self._create_basic_file_info(file_path, language)
                        
                        self.files.append(file_info)
                        analyzed_count += 1
                    else:
                        # Use cached version
                        relative_path = str(file_path.relative_to(self.root_path))
                        cached_file = self.file_cache[relative_path]
                        self.files.append(cached_file)
                        cached_count += 1
        
        print(f"📊 Analyzed {analyzed_count} files, used cache for {cached_count} files")
        print(f"⏭️  Skipped {skipped_vendor} vendor files, {skipped_minified} minified files, {skipped_oversized} oversized files")
        
        # Save updated cache
        self._save_cache()
        
        # Analyze database if config provided
        if self.db_config:
            print(f"🗄️  Analyzing {self.db_type.upper()} database schema...")
            self.analyze_database_schema()

    def _is_basic_ignore(self, path: Path) -> bool:
        """Check basic ignore patterns (original logic)."""
        path_str = str(path)
        name = path.name
        
        for pattern in self.ignore_patterns:
            if pattern.startswith('*.'):
                if name.endswith(pattern[1:]):
                    return True
            elif pattern in path_str or name == pattern:
                return True
        return False

    def _is_vendor_by_content(self, file_path: Path) -> bool:
        """Check file content for vendor/library markers."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Only read first 1KB for performance
                header = f.read(1024)
                
            if self._contains_vendor_markers(header):
                return True
                
        except:
            pass
        return False

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
                        'doc': f.docstring[:100] if f.docstring else None,
                        'return_type': f.return_type
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
            
            # C/C++ specific info
            if file_info.includes:
                summary['includes'] = file_info.includes[:15]  # System headers
            
            if file_info.defines:
                summary['defines'] = file_info.defines[:10]  # Preprocessor macros
            
            if file_info.structs:
                summary['structs'] = [
                    {
                        'name': s.name,
                        'members': s.members[:10],
                        'is_typedef': s.is_typedef
                    } for s in file_info.structs[:10]
                ]
            
            if file_info.cpp_classes:
                summary['cpp_classes'] = [
                    {
                        'name': c.name,
                        'methods': [m.name for m in c.methods[:15]],
                        'bases': c.bases,
                        'is_template': c.is_template,
                        'template_params': c.template_params
                    } for c in file_info.cpp_classes[:10]
                ]
            
            if file_info.namespaces:
                summary['namespaces'] = file_info.namespaces[:10]
            
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

    def add_library_pattern(self, pattern: str) -> None:
        """Add a custom library pattern to ignore."""
        self.library_patterns.add(pattern)

    def remove_library_pattern(self, pattern: str) -> None:
        """Remove a library pattern."""
        self.library_patterns.discard(pattern)

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
        
        # C/C++ summary
        c_files = [f for f in self.files if f.language in ['c', 'c_header'] and (f.functions or f.structs)]
        cpp_files = [f for f in self.files if f.language in ['cpp', 'cpp_header'] and (f.functions or f.cpp_classes)]
        
        if c_files:
            total_functions = sum(len(f.functions) for f in c_files)
            total_structs = sum(len(f.structs) for f in c_files)
            common_includes = set()
            for f in c_files:
                common_includes.update(f.includes[:5])  # Top includes
            
            print(f"\n🔧 C Code: {total_functions} functions, {total_structs} structs")
            if common_includes:
                print(f"  Common includes: {', '.join(sorted(list(common_includes))[:8])}")
        
        if cpp_files:
            total_cpp_classes = sum(len(f.cpp_classes) for f in cpp_files)
            total_cpp_functions = sum(len(f.functions) for f in cpp_files)
            cpp_namespaces = set()
            for f in cpp_files:
                cpp_namespaces.update(f.namespaces)
            
            print(f"\n⚙️  C++ Code: {total_cpp_classes} classes, {total_cpp_functions} functions")
            if cpp_namespaces:
                print(f"  Namespaces: {', '.join(sorted(cpp_namespaces))}")
        
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
    parser.add_argument('--ignore-library', action='append',
                       help='Additional library patterns to ignore (e.g., "my-custom-lib")')
    parser.add_argument('--summary', action='store_true', 
                       help='Print summary to console')
    parser.add_argument('--force-refresh', action='store_true',
                       help='Force re-analysis of all files (ignore cache)')
    parser.add_argument('--cache-file', default='.codebase_cache.json',
                       help='Cache file name (default: .codebase_cache.json)')
    parser.add_argument('--max-file-size', type=int, default=500,
                       help='Max file size in KB to analyze (default: 500KB)')
    
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
    
    # Customize file size threshold
    indexer.max_vendor_file_size = args.max_file_size * 1024
    
    # Add custom ignore patterns
    if args.ignore:
        for pattern in args.ignore:
            indexer.add_ignore_pattern(pattern)
    
    # Add custom library patterns
    if args.ignore_library:
        for pattern in args.ignore_library:
            indexer.add_library_pattern(pattern)
    
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