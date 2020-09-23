# Ansible collection code generator

We use this repository to generate the ansible modules.

## Requirements

You need the following components on your system:

- python 3.8.2
- tox

## Usage

To build the modules:

1. add new swagger file at: src/swagger/<vendor>/<app>
2. generate modules
    ```bash
    tox -e refresh_modules
    ```

The modules will be generated in `build` subdirectory.
