# Contributing to build123d-techdraw

Thank you for your interest in contributing to `build123d-techdraw`!

---

## 🛠️ Development Setup

We use [`uv`](https://github.com/astral-sh/uv) for fast, deterministic Python environment and dependency management.

```bash
# Clone the repository
git clone https://github.com/szubster/build123d-techdraw.git
cd build123d-techdraw

# Install dependencies into virtual environment
uv sync --all-groups

# Run tests
uv run pytest

# Run linter & formatter
uv run ruff check
uv run ruff format --check

# Run static type checker
uv run mypy src
```

---

## 📝 Conventional Commits & Release Management

We follow the [Conventional Commits](https://www.conventionalcommits.org/) specification (v1.0.0). Every commit message and pull request title must follow this format:

```text
<type>(<scope>): <short description>
```

### Allowed Types
- **`feat:`** A new feature (triggers a MINOR version bump).
- **`fix:`** A bug fix (triggers a PATCH version bump).
- **`docs:`** Documentation changes.
- **`refactor:`** Code changes that neither fix a bug nor add a feature.
- **`test:`** Adding or refactoring tests.
- **`chore:`** Maintenance, build configuration, or dependency updates.
- **`ci:`** Changes to GitHub Actions or CI scripts.

### Breaking Changes
Indicate breaking changes with a `!` after the type/scope (e.g., `feat(core)!: change projection API signature`) or by including `BREAKING CHANGE:` in the commit footer. This triggers a MAJOR version bump.

### Examples
- `feat(core): add ISO A3 and A4 sheet templates with border markings`
- `fix(renderers): correct line cap styling in dark blueprint SVG export`
- `docs: add interactive preview examples to README`
- `test(dimensions): add test coverage for diameter callout geometry`

---

## 🧪 Testing & Code Quality Requirements

Before submitting a pull request, ensure all checks pass:

1. **Linting & Formatting:**
   ```bash
   uv run ruff check --fix
   uv run ruff format
   ```
2. **Type Checking:** Full strict type annotations are required on all public classes, functions, and module interfaces:
   ```bash
   uv run mypy src
   ```
3. **Tests & Coverage:**
   ```bash
   uv run pytest
   ```
