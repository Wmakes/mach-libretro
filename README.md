# mach-libretro

**Native libretro bindings for the Mach programming language.**

mach-libretro provides low-level bindings for the [libretro API](https://www.libretro.com/) in the [Mach programming language](https://machlang.org/).

The goal is to enable developers to write libretro cores entirely in Mach, without requiring a C wrapper.

## Usage

Add this repo to your project by running:

```sh
mach dep add . libretro --git https://github.com/Wmakes/mach-libretro
```

Or add it as a dependency in your `mach.toml` by hand:

```toml
[dep.libretro]
git = "https://github.com/Wmakes/mach-libretro"
ref = "branch/main"
```

## Contributing

Contributions, bug reports, and suggestions are welcome.

## Acknowledgments

mach-libretro targets the libretro API, developed and maintained by the libretro contributors.

This is an independent project and is not officially affiliated with or endorsed by the libretro or RetroArch projects.

## License

mach-libretro is licensed under the **MIT License**.

Copyright © 2026 Wmakes.

See the [LICENSE](LICENSE) file for details.
