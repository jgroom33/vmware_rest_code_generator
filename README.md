# Ansible collection code generator

We use this repository to generate the ansible modules.

## Requirements

You need the following components on your system:

- python 3.6
- tox

## Usage

To build the modules:

```bash
tox -e refresh_modules
```

The modules will be generated in `build` subdirectory.
