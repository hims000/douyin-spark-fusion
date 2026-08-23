# Contributing to Fusion Selfhosted

Thank you for your interest in contributing! This document outlines the process for contributing to the project.

## Code of Conduct

This project adheres to a Code of Conduct. By participating, you are expected to uphold this code. Please report unacceptable behavior to the project maintainers.

## How to Contribute

1. **Fork the repository** and clone it locally.
2. **Create a branch** for your changes (`git checkout -b feature/your-feature`).
3. **Make your changes** and ensure they follow the project's style guide.
4. **Test your changes** thoroughly.
5. **Submit a pull request** with a clear description of your changes.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/your-org/fusion-selfhosted.git
   cd fusion-selfhosted
   ```

2. Create a virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python app.py
   ```

## Pull Request Process

1. Ensure your PR includes a clear description of the changes and the motivation behind them.
2. Reference any related issues using the `#issue-number` syntax.
3. Ensure all tests pass before submitting.
4. Update the CHANGELOG.md with your changes under the appropriate version.
5. A maintainer will review your PR. Address any feedback and respond to comments.

## Style Guide

- Follow PEP 8 for Python code.
- Use meaningful variable and function names.
- Add docstrings to all public functions and classes.
- Keep functions small and focused on a single responsibility.
- Write tests for new functionality.