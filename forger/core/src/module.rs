//! Python module resolution.
//!
//! Implements Python's import semantics for finding modules and packages
//! based on sys.path, package structure, and importlib conventions.

use std::collections::HashSet;
use std::path::{Path, PathBuf};

use crate::filesystem::{FileDiscovery, FileType};
use crate::pathutil::normalize_path;
use crate::result::{ForgerError, ForgerResult};

/// A resolved module specification.
#[derive(Debug, Clone)]
pub struct ModuleSpec {
    /// Fully qualified module name (e.g., "os.path").
    pub name: String,
    /// Type of module.
    pub module_type: ModuleType,
    /// File system path to the module or package.
    pub path: PathBuf,
    /// Whether this is part of the standard library.
    pub is_stdlib: bool,
    /// Parent package name (if any).
    pub parent: Option<String>,
    /// Package submodules discovered alongside this module.
    pub submodules: Vec<String>,
}

/// Classification of a Python module.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModuleType {
    /// A single .py file.
    PythonModule,
    /// A directory with __init__.py.
    PythonPackage,
    /// A namespace package (no __init__.py).
    NamespacePackage,
    /// A built-in module implemented in C.
    BuiltIn,
    /// A frozen module.
    Frozen,
    /// A compiled extension (.so, .pyd, .dylib).
    Extension,
}

/// Python module resolver.
pub struct ModuleResolver {
    /// Search paths (sys.path equivalent).
    search_paths: Vec<PathBuf>,
    /// Known standard library roots.
    stdlib_roots: Vec<PathBuf>,
    /// Minimum supported Python version.
    #[allow(dead_code)]
    min_python_version: (u32, u32),
}

impl ModuleResolver {
    /// Create a new module resolver.
    pub fn new(search_paths: Vec<PathBuf>) -> Self {
        Self {
            search_paths,
            stdlib_roots: Vec::new(),
            min_python_version: (3, 10),
        }
    }

    /// Set the standard library roots.
    pub fn with_stdlib_roots(mut self, roots: Vec<PathBuf>) -> Self {
        self.stdlib_roots = roots;
        self
    }

    /// Resolve a module name to its file system location.
    ///
    /// Follows Python's import semantics:
    /// 1. Check for built-in modules
    /// 2. Search sys.path for regular packages, namespace packages, and single-file modules
    pub fn resolve(&self, module_name: &str) -> ForgerResult<Option<ModuleSpec>> {
        // Check built-ins first
        if Self::is_builtin(module_name) {
            return Ok(Some(ModuleSpec {
                name: module_name.into(),
                module_type: ModuleType::BuiltIn,
                path: PathBuf::new(),
                is_stdlib: true,
                parent: Self::get_parent(module_name),
                submodules: Vec::new(),
            }));
        }

        // Search each path in sys.path order
        for search_path in &self.search_paths {
            if let Some(spec) = self.try_resolve_at(search_path, module_name)? {
                return Ok(Some(spec));
            }
        }

        Ok(None)
    }

    /// Try to resolve a module at a specific search path.
    fn try_resolve_at(
        &self,
        search_path: &Path,
        module_name: &str,
    ) -> ForgerResult<Option<ModuleSpec>> {
        let parts: Vec<&str> = module_name.split('.').collect();
        let mut current_path = search_path.to_path_buf();

        for (i, part) in parts.iter().enumerate() {
            let mut is_package = false;

            // Check for regular package (directory with __init__.py)
            let package_path = current_path.join(Path::new(*part));
            if package_path.is_dir() {
                let init_path = package_path.join("__init__.py");
                let init_pyc_path = package_path.join("__init__.pyc");
                if init_path.exists() || init_pyc_path.exists() {
                    is_package = true;
                    current_path = package_path;
                } else {
                    // Namespace package (directory without __init__.py)
                    let spec = ModuleSpec {
                        name: module_name.into(),
                        module_type: ModuleType::NamespacePackage,
                        path: package_path.clone(),
                        is_stdlib: self.is_stdlib_path(&package_path),
                        parent: if i > 0 {
                            Some(parts[..i].join("."))
                        } else {
                            None
                        },
                        submodules: self.discover_submodules(&package_path)?,
                    };
                    return Ok(Some(spec));
                }
            }

            // Check for single-file module
            if !is_package {
                let module_py = current_path.join(format!("{}.py", part));
                let module_pyc = current_path.join(format!("{}.pyc", part));
                let module_ext = current_path.join(format!("{}.pyd", part));
                let module_so = current_path.join(format!("{}.so", part));

                if module_py.exists() {
                    current_path = module_py;
                    let is_stdlib = self.is_stdlib_path(&current_path);
                    return Ok(Some(ModuleSpec {
                        name: module_name.into(),
                        module_type: ModuleType::PythonModule,
                        path: current_path.clone(),
                        is_stdlib,
                        parent: if i > 0 {
                            Some(parts[..i].join("."))
                        } else {
                            None
                        },
                        submodules: Vec::new(),
                    }));
                }

                if module_pyc.exists() {
                    return Ok(Some(ModuleSpec {
                        name: module_name.into(),
                        module_type: ModuleType::PythonModule,
                        path: module_pyc.clone(),
                        is_stdlib: self.is_stdlib_path(&module_pyc),
                        parent: if i > 0 {
                            Some(parts[..i].join("."))
                        } else {
                            None
                        },
                        submodules: Vec::new(),
                    }));
                }

                if module_ext.exists() {
                    return Ok(Some(ModuleSpec {
                        name: module_name.into(),
                        module_type: ModuleType::Extension,
                        path: module_ext.clone(),
                        is_stdlib: self.is_stdlib_path(&module_ext),
                        parent: if i > 0 {
                            Some(parts[..i].join("."))
                        } else {
                            None
                        },
                        submodules: Vec::new(),
                    }));
                }

                if module_so.exists() {
                    return Ok(Some(ModuleSpec {
                        name: module_name.into(),
                        module_type: ModuleType::Extension,
                        path: module_so.clone(),
                        is_stdlib: self.is_stdlib_path(&module_so),
                        parent: if i > 0 {
                            Some(parts[..i].join("."))
                        } else {
                            None
                        },
                        submodules: Vec::new(),
                    }));
                }

                // If we were in a package, maybe the submodule doesn't exist
                if is_package {
                    return Ok(None);
                }
            }
        }

        // If we traversed all parts and ended on a package
        if current_path.is_dir() {
            let init_exists = current_path.join("__init__.py").exists();
            if init_exists {
                return Ok(Some(ModuleSpec {
                    name: module_name.into(),
                    module_type: ModuleType::PythonPackage,
                    path: current_path.clone(),
                    is_stdlib: self.is_stdlib_path(&current_path),
                    parent: None,
                    submodules: self.discover_submodules(&current_path)?,
                }));
            }
        }

        Ok(None)
    }

    /// Discover submodules in a package directory.
    fn discover_submodules(&self, package_path: &Path) -> ForgerResult<Vec<String>> {
        let mut submodules = Vec::new();

        if !package_path.is_dir() {
            return Ok(submodules);
        }

        for entry in std::fs::read_dir(package_path).map_err(|e| {
            ForgerError::Filesystem(format!("cannot read directory {:?}: {e}", package_path))
        })? {
            let entry = entry.map_err(|e| {
                ForgerError::Filesystem(format!("directory entry error: {e}"))
            })?;
            let path = entry.path();
            let name = entry.file_name();
            let name_str = name.to_string_lossy().to_string();

            // Skip hidden files and cache directories
            if name_str.starts_with('.') || name_str.starts_with('_') && name_str != "__init__" {
                continue;
            }

            if path.is_dir() {
                // Check if it's a package
                let init_py = path.join("__init__.py");
                let init_pyc = path.join("__init__.pyc");
                if init_py.exists() || init_pyc.exists() {
                    submodules.push(name_str);
                }
            } else if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
                // Single-file module
                if ext == "py" || ext == "pyc" || ext == "pyd" || ext == "so" {
                    let stem = path
                        .file_stem()
                        .map(|s| s.to_string_lossy().to_string())
                        .unwrap_or_default();
                    if !stem.is_empty() && !stem.starts_with('.') {
                        submodules.push(stem);
                    }
                }
            }
        }

        Ok(submodules)
    }

    /// Check if a path is under a standard library root.
    fn is_stdlib_path(&self, path: &Path) -> bool {
        for root in &self.stdlib_roots {
            if path.starts_with(root) {
                return true;
            }
        }

        // Heuristic: check if path contains common stdlib markers
        let normalized = normalize_path(path);
        for marker in ["/lib/python3", "\\Lib\\", "\\lib\\python"] {
            if normalized.contains(marker) {
                return true;
            }
        }

        false
    }

    /// Check if a module name is a built-in module.
    fn is_builtin(name: &str) -> bool {
        // Common built-in modules in CPython
        const BUILTINS: &[&str] = &[
            "builtins",
            "marshal",
            "imp",
            "_imp",
            "_thread",
            "sys",
            "gc",
            "_warnings",
            "_thread",
            "errno",
            "atexit",
            "_frozen_importlib",
            "_frozen_importlib_external",
            "zipimport",
            "_codecs",
            "_weakref",
            "_functools",
            "_operator",
            "_signal",
            "_stat",
            "_collections",
            "_io",
            "_sre",
            "_datetime",
            "_locale",
            "_pickle",
            "_struct",
            "_tracemalloc",
            "_abc",
            "faulthandler",
            "_compression",
            "_posixsubprocess",
            "mmap",
            "posix",
            "nt",
            "_winapi",
            "winreg",
            "msvcrt",
        ];

        BUILTINS.contains(&name)
    }

    /// Get the parent package name.
    fn get_parent(module_name: &str) -> Option<String> {
        module_name.split('.').collect::<Vec<&str>>()[..]
            .len()
            .checked_sub(2)
            .map(|end| module_name.split('.').take(end + 1).collect::<Vec<&str>>().join("."))
    }

    /// Resolve multiple module names in batch.
    pub fn resolve_batch(
        &self,
        module_names: &[String],
    ) -> ForgerResult<Vec<Option<ModuleSpec>>> {
        module_names
            .iter()
            .map(|name| self.resolve(name))
            .collect::<ForgerResult<Vec<_>>>()
    }

    /// Find all Python modules in the search paths (for initial discovery).
    pub fn discover_all_modules(&self) -> ForgerResult<Vec<ModuleSpec>> {
        let mut specs = Vec::new();
        let mut seen = HashSet::new();

        for search_path in &self.search_paths {
            if !search_path.exists() {
                continue;
            }

            let options = crate::filesystem::DiscoveryOptions {
                root: search_path.to_path_buf(),
                include_patterns: vec![],
                ..Default::default()
            };

            let discovery = FileDiscovery::new(options);
            let entries = discovery.discover()?;

            for entry in entries {
                if entry.file_type != FileType::PythonSource
                    && entry.file_type != FileType::NativeExtension
                {
                    continue;
                }

                // Convert relative path to module name
                let relative = entry
                    .relative
                    .strip_prefix(search_path)
                    .unwrap_or(&entry.relative);

                let module_name = Self::path_to_module_name(relative);
                if module_name.is_empty() || !seen.insert(module_name.clone()) {
                    continue;
                }

                let is_stdlib = self.is_stdlib_path(&entry.path);
                let module_type = if entry.file_type == FileType::NativeExtension {
                    ModuleType::Extension
                } else {
                    ModuleType::PythonModule
                };

                specs.push(ModuleSpec {
                    name: module_name,
                    module_type,
                    path: entry.path,
                    is_stdlib,
                    parent: None,
                    submodules: Vec::new(),
                });
            }
        }

        Ok(specs)
    }

    /// Convert a file path to a Python module name.
    fn path_to_module_name(path: &Path) -> String {
        let mut parts = Vec::new();

        for component in path.components() {
            if let std::path::Component::Normal(c) = component {
                let name = c.to_string_lossy();
                // Skip .pyc files
                if name.ends_with(".pyc") {
                    continue;
                }
                // Strip extensions
                let stem = name
                    .strip_suffix(".py")
                    .or_else(|| name.strip_suffix(".pyw"))
                    .or_else(|| name.strip_suffix(".pyd"))
                    .or_else(|| name.strip_suffix(".so"))
                    .unwrap_or(&name);

                // Handle __init__ - skip it (package is represented by the directory)
                if stem == "__init__" {
                    continue;
                }

                if !stem.is_empty() {
                    parts.push(stem.to_string());
                }
            }
        }

        parts.join(".")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_is_builtin() {
        assert!(ModuleResolver::is_builtin("sys"));
        assert!(ModuleResolver::is_builtin("builtins"));
        assert!(!ModuleResolver::is_builtin("django"));
        assert!(!ModuleResolver::is_builtin("myapp"));
    }

    #[test]
    fn test_resolve_single_file_module() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        std::fs::write(tmp.path().join("mymodule.py"), "x = 1").unwrap();

        let resolver = ModuleResolver::new(vec![tmp.path().to_path_buf()]);
        let spec = resolver.resolve("mymodule").unwrap();

        assert!(spec.is_some());
        let spec = spec.unwrap();
        assert_eq!(spec.name, "mymodule");
        assert_eq!(spec.module_type, ModuleType::PythonModule);
    }

    #[test]
    fn test_resolve_package() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let pkg_dir = tmp.path().join("mypackage");
        std::fs::create_dir_all(&pkg_dir).unwrap();
        std::fs::write(pkg_dir.join("__init__.py"), "").unwrap();
        std::fs::write(pkg_dir.join("submodule.py"), "y = 2").unwrap();

        let resolver = ModuleResolver::new(vec![tmp.path().to_path_buf()]);
        let spec = resolver.resolve("mypackage").unwrap();

        assert!(spec.is_some());
        let spec = spec.unwrap();
        assert_eq!(spec.name, "mypackage");
        assert_eq!(spec.module_type, ModuleType::PythonPackage);
        assert!(spec.submodules.contains(&"submodule".to_string()));
    }

    #[test]
    fn test_resolve_nonexistent() {
        let tmp = TempDir::with_prefix("forger-test").unwrap();
        let resolver = ModuleResolver::new(vec![tmp.path().to_path_buf()]);
        let spec = resolver.resolve("nonexistent_module").unwrap();

        assert!(spec.is_none());
    }

    #[test]
    fn test_path_to_module_name() {
        assert_eq!(
            ModuleResolver::path_to_module_name(Path::new("os/path.py")),
            "os.path"
        );
        assert_eq!(
            ModuleResolver::path_to_module_name(Path::new("mypackage/__init__.py")),
            "mypackage"
        );
        assert_eq!(
            ModuleResolver::path_to_module_name(Path::new("single.py")),
            "single"
        );
    }

    #[test]
    fn test_get_parent() {
        assert_eq!(ModuleResolver::get_parent("os"), None);
        assert_eq!(
            ModuleResolver::get_parent("os.path"),
            Some("os".to_string())
        );
        assert_eq!(
            ModuleResolver::get_parent("django.contrib.auth"),
            Some("django.contrib".to_string())
        );
    }
}
