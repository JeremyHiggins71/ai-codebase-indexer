from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="ai-codebase-indexer",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Create AI-friendly indexes of codebases with smart vendor filtering",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/ai-codebase-indexer",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Code Generators",
        "Topic :: Software Development :: Documentation",
        "Topic :: Utilities",
    ],
    python_requires=">=3.8",
    install_requires=[
        # Core has no dependencies - everything is optional
    ],
    extras_require={
        "mysql": ["mysql-connector-python>=8.0.0"],
        "postgresql": ["psycopg2-binary>=2.9.0"],
        "enhanced": [
            "tree-sitter>=0.20.0",
            "tree-sitter-languages>=1.10.2",
        ],
        "full": [
            "mysql-connector-python>=8.0.0",
            "psycopg2-binary>=2.9.0",
            "tree-sitter>=0.20.0",
            "tree-sitter-languages>=1.10.2",
        ]
    },
    entry_points={
        "console_scripts": [
            "codebase-indexer=codebase_indexer:main",
        ],
    },
    project_urls={
        "Bug Reports": "https://github.com/yourusername/ai-codebase-indexer/issues",
        "Source": "https://github.com/yourusername/ai-codebase-indexer",
    },
)