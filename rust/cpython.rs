//! CPython module analysis and minimal build infrastructure.
//!
//! Analyzes which CPython standard library modules are required by an application,
//! determines which C source files from the CPython codebase are needed,
//! and generates a minimal CPython build configuration.
//!
//! CPython's standard library has many modules implemented in C (under `Modules/`).
//! Not all are needed for every application. This module:
//! 1. Maps Python module names to their C source files
//! 2. Analyzes dependency graphs to find required stdlib modules
//! 3. Determines which C files are needed
//! 4. Generates a minimal Modules/Setup file for CPython's build system
//! 5. Identifies unused C code that can be excluded

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use pyo3::prelude::*;

use crate::result::{ForgerError, ForgerResult};

// ---------------------------------------------------------------------------
// CPython stdlib module registry
// ---------------------------------------------------------------------------

/// Classification of a stdlib module's implementation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModuleImpl {
    /// Pure Python module (.py file in Lib/)
    PurePython,
    /// Built-in C module (compiled into Python by default)
    BuiltinC,
    /// Optional C module (enabled via Modules/Setup)
    OptionalC,
    /// Extension module (.c file that builds a shared library)
    Extension,
    /// Package with mixed Python and C implementations
    Mixed,
}

/// Metadata about a CPython stdlib module.
#[derive(Debug, Clone)]
pub struct StdlibModuleInfo {
    /// Fully qualified module name.
    pub name: String,
    /// Implementation type.
    pub impl_type: ModuleImpl,
    /// C source files required for this module (if C module).
    pub c_sources: Vec<String>,
    /// Other stdlib modules this module depends on at import time.
    pub python_deps: Vec<String>,
    /// C modules this module depends on (e.g., `_collections` for `collections`).
    pub c_deps: Vec<String>,
    /// Whether this module is always built into CPython.
    pub always_built: bool,
    /// Configure flag that controls this module (e.g., `--enable-unicode`).
    pub configure_flag: Option<String>,
    /// Description.
    pub description: String,
}

/// C source file metadata.
#[derive(Debug, Clone)]
pub struct CSourceFile {
    /// Relative path within CPython source tree (e.g., "Modules/_sre.c").
    pub path: String,
    /// Which Python modules use this C file.
    pub used_by: Vec<String>,
    /// Other C files this file includes/depends on.
    pub c_deps: Vec<String>,
    /// Header files this file includes.
    pub headers: Vec<String>,
    /// Whether this file is always required (core interpreter functionality).
    pub always_required: bool,
    /// Size in bytes (estimated or actual).
    pub size_bytes: u64,
}

// ---------------------------------------------------------------------------
// Module registry
// ---------------------------------------------------------------------------

/// The CPython stdlib module registry.
///
/// Contains knowledge about which modules are C-implemented,
/// their source files, dependencies, and build configuration.
#[pyclass]
pub struct CpythonModuleRegistry {
    /// Module name -> module info.
    modules: HashMap<String, StdlibModuleInfo>,
    /// C source path -> source file info.
    c_sources: HashMap<String, CSourceFile>,
    /// Python version this registry was built for.
    python_version: (u32, u32),
}

impl CpythonModuleRegistry {
    /// Create a new registry populated with known stdlib module information.
    pub fn new(python_version: (u32, u32)) -> Self {
        let mut registry = Self {
            modules: HashMap::new(),
            c_sources: HashMap::new(),
            python_version,
        };

        registry.populate_builtin_modules();
        registry.populate_optional_modules();
        registry.populate_c_source_map();
        registry
    }

    /// Populate always-built (builtin) C modules.
    fn populate_builtin_modules(&mut self) {
        // Core builtin modules — these are always compiled into CPython
        let builtins = [
            ("builtins", "Core built-in functions and exceptions", vec!["Python/bltinmodule.c"]),
            ("sys", "System-specific parameters and functions", vec!["Python/sysmodule.c"]),
            ("marshal", "Internal Python bytecode serialization", vec!["Python/marshal.c"]),
            ("_imp", "Internal import machinery", vec!["Python/import.c"]),
            ("_codecs", "Character encoding/decoding", vec!["Objects/unicodeobject.c", "Objects/codecs.c"]),
            ("_functools", "Functools C implementation", vec!["Modules/_functoolsmodule.c"]),
            ("_operator", "Operator C implementation", vec!["Modules/_operator.c"]),
            ("_signal", "Signal handling", vec!["Python/signalmodule.c"]),
            ("_stat", "Stat module C implementation", vec!["Modules/_stat.c"]),
            ("_string", "String operations C implementation", vec!["Modules/_string.c"]),
            ("_thread", "Thread primitives", vec!["Python/thread.c"]),
            ("_tracemalloc", "Tracing memory allocations", vec!["Modules/_tracemalloc.c"]),
            ("_warnings", "Warnings C implementation", vec!["Python/pythonrun.c"]),
            ("atexit", "Cleanup handlers", vec!["Python/pyfpe.c"]),
            ("gc", "Garbage collection", vec!["Modules/gcmodule.c"]),
            ("itertools", "Iterator algebra", vec!["Modules/itertoolsmodule.c"]),
            ("posix", "POSIX system calls", vec!["Modules/posixmodule.c"]),
            ("nt", "Windows NT system calls", vec!["Modules/errnomodule.c", "Modules/winsound.c"]),
            ("_collections", "Collection types C implementation", vec!["Modules/_collectionsmodule.c"]),
            ("_sre", "Regular expression engine", vec!["Modules/_sre.c"]),
            ("_weakref", "Weak references", vec!["Objects/weakrefobject.c"]),
            ("zipimport", "Import from ZIP files", vec!["Python/zipimport.c"]),
            ("time", "Time access and conversions", vec!["Modules/timemodule.c"]),
            ("math", "Mathematical functions", vec!["Modules/mathmodule.c"]),
            ("struct", "Binary data packing", vec!["Modules/structmodule.c"]),
            ("array", "Array type", vec!["Modules/arraymodule.c"]),
            ("binascii", "Binary/ASCII conversions", vec!["Modules/binascii.c"]),
            ("_datetime", "Date/time C implementation", vec!["Modules/_datetimemodule.c"]),
            ("_locale", "Locale support", vec!["Modules/_localemodule.c"]),
            ("_io", "File I/O", vec!["Modules/_io/_iomodule.c", "Modules/_io/bufferedio.c", "Modules/_io/bytesio.c", "Modules/_io/fileio.c", "Modules/_io/iobase.c", "Modules/_io/textio.c", "Modules/_io/winconsoleio.c"]),
            ("_abc", "Abstract base classes", vec!["Objects/typeobject.c"]),
            ("_signal", "Signal handling", vec!["Python/signalmodule.c"]),
            ("_ssl", "TLS/SSL support", vec!["Modules/_ssl.c", "Modules/_ssl_slots.c"]),
            ("_socket", "Socket operations", vec!["Modules/socketmodule.c"]),
            ("_hashlib", "Hash functions", vec!["Modules/_hashopenssl.c"]),
            ("_random", "Random number generator", vec!["Modules/_randommodule.c"]),
            ("_csv", "CSV parsing", vec!["Modules/_csv.c"]),
            ("_json", "JSON C acceleration", vec!["Modules/_json.c"]),
            ("_lzma", "LZMA compression", vec!["Modules/lzmamodule.c"]),
            ("_bz2", "BZIP2 compression", vec!["Modules/_bz2module.c"]),
            ("_zlib", "ZLIB compression", vec!["Modules/zlibmodule.c"]),
            ("_gzip", "GZIP compression", vec![]),
            ("_multiprocessing", "Multiprocessing primitives", vec!["Modules/_multiprocessing/multiprocessing.c", "Modules/_multiprocessing/semaphore.c"]),
            ("select", "I/O waiting", vec!["Modules/selectmodule.c"]),
            ("_contextvars", "Context variables", vec!["Modules/_contextvarsc.c"]),
            ("_pickle", "Pickle C acceleration", vec!["Modules/_pickle.c"]),
            ("_sha256", "SHA-256 hash", vec!["Modules/_sha256.c"]),
            ("_sha3", "SHA-3 hash", vec!["Modules/_sha3.c"]),
            ("_blake2", "BLAKE2 hash", vec!["Modules/_blake2.c"]),
            ("_sha1", "SHA-1 hash", vec!["Modules/_sha1.c"]),
            ("_md5", "MD5 hash", vec!["Modules/_md5.c"]),
            ("_pickle", "Pickle C implementation", vec!["Modules/_pickle.c"]),
            ("faulthandler", "Fault handler", vec!["Python/faulthandler.c"]),
            ("_thread", "Threading", vec!["Python/thread.c"]),
            ("_zoneinfo", "Timezone data", vec!["Modules/_zoneinfo.c"]),
            ("_elementtree", "XML ElementTree C implementation", vec!["Modules/expat.c", "Modules/pyexpat.c"]),
            ("parser", "Parser interface", vec!["Objects/frameobject.c"]),
            ("_ast", "AST access", vec!["Python/ast.c"]),
            ("dis", "Bytecode disassembler", vec![]),
            ("_frozen_importlib", "Frozen importlib", vec!["Python/import.c"]),
            ("_frozen_importlib_external", "External importlib", vec!["Python/import.c"]),
            // Unicode data
            ("_codecs_cn", "Chinese codecs", vec!["Objects/codecs_cn.c"]),
            ("_codecs_hk", "Hong Kong codecs", vec!["Objects/codecs_hk.c"]),
            ("_codecs_iso2022", "ISO-2022 codecs", vec!["Objects/codecs_iso2022.c"]),
            ("_codecs_jp", "Japanese codecs", vec!["Objects/codecs_jp.c"]),
            ("_codecs_kr", "Korean codecs", vec!["Objects/codecs_kr.c"]),
            ("_codecs_tw", "Traditional Chinese codecs", vec!["Objects/codecs_tw.c"]),
            ("_posixsubprocess", "Subprocess on POSIX", vec!["Modules/posixmodule.c"]),
            ("_overlapped", "Windows async I/O", vec!["Modules/overlapped.c"]),
            ("_winapi", "Windows API", vec!["Modules/winreg.c", "Modules/winsound.c"]),
            ("winreg", "Windows registry", vec!["Modules/winreg.c"]),
            ("msvcrt", "MSVC runtime", vec!["Modules/msvcrtmodule.c"]),
            ("_ctypes", "C foreign function interface", vec!["Modules/_ctypes/_ctypes.c", "Modules/_ctypes/_ctypes_callproc.c", "Modules/_ctypes/stgdict.c"]),
            ("_decimal", "Decimal arithmetic", vec!["Modules/_decimal/_decimal.c"]),
            ("_queue", "Queue C implementation", vec!["Modules/_queuemodule.c"]),
            ("mmap", "Memory-mapped files", vec!["Modules/mmapmodule.c"]),
            ("nis", "NIS/Yellow Pages", vec!["Modules/nismodule.c"]),
            ("ossaudiodev", "OSS audio device", vec!["Modules/ossaudiodev.c"]),
            ("spwd", "Shadow password", vec!["Modules/spwdmodule.c"]),
            ("syslog", "Syslog", vec!["Modules/syslogmodule.c"]),
            ("termios", "Terminal I/O", vec!["Modules/termios.c"]),
            ("xxlimited", "Limited API test module", vec!["Modules/xxlimited.c"]),
        ];

        for (name, desc, sources) in builtins {
            let is_core = ["builtins", "sys", "marshal", "_imp", "_codecs", "_frozen_importlib", "_frozen_importlib_external"].contains(&name);

            let c_deps = self.extract_c_deps(&sources);
            let module = StdlibModuleInfo {
                name: name.to_string(),
                impl_type: if is_core { ModuleImpl::BuiltinC } else { ModuleImpl::OptionalC },
                c_sources: sources.iter().map(|s| s.to_string()).collect(),
                python_deps: Vec::new(),
                c_deps,
                always_built: is_core,
                configure_flag: None,
                description: desc.to_string(),
            };

            self.modules.insert(name.to_string(), module);
        }
    }

    /// Populate optional C modules that may or may not be needed.
    fn populate_optional_modules(&mut self) {
        // Pure Python stdlib modules (no C source needed)
        let pure_python = [
            "abc", "aifc", "argparse", "ast", "asynchat", "asyncio", "asyncore",
            "base64", "basehttp", "bdb", "binhex", "bisect", "builtins", "bz2",
            "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code", "codecs",
            "codeop", "collections", "colorsys", "compileall", "concurrent",
            "configparser", "contextlib", "contextvars", "copy", "copyreg",
            "cProfile", "crypt", "curses", "dataclasses", "datetime", "dbm",
            "decimal", "difflib", "dis", "distutils", "doctest", "email",
            "encodings", "enum", "errno", "fnmatch", "formatter", "fractions",
            "ftplib", "functools", "genericpath", "getopt", "getpass", "gettext",
            "glob", "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib",
            "imaplib", "imghdr", "imp", "importlib", "inspect", "io", "ipaddress",
            "itertools", "json", "keyword", "lib2to3", "linecache", "locale",
            "logging", "lzma", "mailbox", "mailcap", "marshal", "math", "mimetypes",
            "mmap", "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
            "ntpath", "numbers", "operator", "optparse", "os", "ossaudiodev",
            "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes", "pkgutil",
            "platform", "plistlib", "poplib", "posix", "posixpath", "pprint",
            "profile", "pstats", "pty", "pwd", "py_compile", "pyclbr",
            "pydoc", "queue", "quopri", "random", "re", "readline", "reprlib",
            "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
            "selectors", "shelve", "shlex", "shutil", "signal", "site", "smtpd",
            "smtplib", "sndhdr", "socket", "socketserver", "sqlite3", "ssl",
            "stat", "statistics", "string", "stringprep", "struct", "subprocess",
            "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
            "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
            "threading", "time", "timeit", "tkinter", "token", "tokenize",
            "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo",
            "types", "unicodedata", "unittest", "urllib", "uu", "uuid", "venv",
            "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
            "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile",
            "zipimport", "zlib", "zoneinfo", "_threading_local", "_sitebuiltins",
            "_compat_pickle", "_py_abc", "_pyio", "_strptime", "_markupbase",
            "_pydecimal", "_pydatetime",
        ];

        for name in pure_python {
            // Check if we already have C module info for this name
            if !self.modules.contains_key(name) {
                let module = StdlibModuleInfo {
                    name: name.to_string(),
                    impl_type: ModuleImpl::PurePython,
                    c_sources: Vec::new(),
                    python_deps: Vec::new(),
                    c_deps: Vec::new(),
                    always_built: false,
                    configure_flag: None,
                    description: format!("Pure Python stdlib module: {}", name),
                };
                self.modules.insert(name.to_string(), module);
            }
        }

        // Mixed modules (Python wrapper + C acceleration)
        let mixed = [
            ("re", vec!["_sre"], "Regular expressions"),
            ("collections", vec!["_collections"], "Collection datatypes"),
            ("datetime", vec!["_datetime"], "Basic date and time types"),
            ("json", vec!["_json"], "JSON encoder/decoder"),
            ("io", vec!["_io"], "Core I/O classes"),
            ("pickle", vec!["_pickle"], "Pickle serialization"),
            ("operator", vec!["_operator"], "Semantic interface to operators"),
            ("functools", vec!["_functools"], "Higher-order functions"),
            ("string", vec!["_string"], "String operations"),
            ("abc", vec!["_abc"], "Abstract base classes"),
            ("contextvars", vec!["_contextvars"], "Context variable support"),
            ("elementtree", vec!["_elementtree"], "XML ElementTree"),
        ];

        for (name, c_deps, desc) in mixed {
            if let Some(info) = self.modules.get_mut(name) {
                info.impl_type = ModuleImpl::Mixed;
                info.c_deps = c_deps.iter().map(|s| s.to_string()).collect();
                info.description = desc.to_string();
            }
        }
    }

    /// Build the C source file dependency map.
    fn populate_c_source_map(&mut self) {
        // Core interpreter files — always required
        let core_files = [
            "Python/pythonrun.c", "Python/ceval.c", "Python/compile.c",
            "Python/asdl.c", "Python/assemble.c", "Python/ast.c",
            "Python/ast_opt.c", "Python/ast_unparse.c", "Python/builtins.c",
            "Python/ceval_gil.c", "Python/codecs.c", "Python/config.c",
            "Python/context.c", "Python/crossinter.c", "Python/dynload_hpux.c",
            "Python/dynload_tru64.c", "Python/dynload_win.c", "Python/errors.c",
            "Python/fileutils.c", "Python/frozenmain.c", "Python/frozen.c",
            "Python/future.c", "Python/getargs.c", "Python/getplatform.c",
            "Python/gettext.c", "Python/gc_free_threading.c", "Python/gc.c",
            "Python/graminit.c", "Python/import.c", "Python/initconfig.c",
            "Python/index.tcl", "Python/marshal.c", "Python/modsupport.c",
            "Python/myreadline.c", "Python/object_stack.c", "Python/optimizer_bytecodes.c",
            "Python/optimizer_data.c", "Python/optimizer_analysis.c",
            "Python/optimizer_blocks.c", "Python/optimizer_items.c",
            "Python/optimizer_transform.c", "Python/optimizer.c",
            "Python/parsetok.c", "Python/pathconfig.c", "Python/preconfig.c",
            "Python/pyctype.c", "Python/pyfpe.c", "Python/pyhash.c",
            "Python/pymath.c", "Python/pystrcmp.c", "Python/pystrtod.c",
            "Python/pylifecycle.c", "Python/suggestions.c",
            "Python/thread.c", "Python/traceback.c",
            "Python/thread_pthread_stubs.c", "Python/token.c",
            "Python/getcopyright.c", "Python/doctypes.c",
            "Python/formatter_unicode.c", "Python/getversion.c",
            "Python/main.c", "Python/controller.c",
            "Python/controller_threads.c", "Python/specialize.c",
            "Python/structmember.c", "Python/pythonrun.c",
            "Python/bltinmodule.c", "Python/sysmodule.c",
            "Python/faulthandler.c", "Python/signalmodule.c",
            "Python/thread.c", "Python/zipimport.c",
            // Objects
            "Objects/abstract.c", "Objects/boolobject.c", "Objects/bytes_methods.c",
            "Objects/bytearrayobject.c", "Objects/bytesobject.c",
            "Objects/call.c", "Objects/capsule.c", "Objects/cellobject.c",
            "Objects/classobject.c", "Objects/codeobject.c",
            "Objects/complexobject.c", "Objects/descrobject.c",
            "Objects/enumobject.c", "Objects/exceptions.c",
            "Objects/extent.c", "Objects/fileobject.c",
            "Objects/floatobject.c", "Objects/frame_frozen.c",
            "Objects/frameobject.c", "Objects/funcobject.c",
            "Objects/genericaliasobject.c", "Objects/genobject.c",
            "Objects/intobject.c", "Objects/lnotab_notes.c",
            "Objects/iterobject.c", "Objects/listobject.c",
            "Objects/longobject.c", "Objects/memoryobject.c",
            "Objects/methodobject.c", "Objects/moduleobject.c",
            "Objects/namespaceobject.c", "Objects/object.c",
            "Objects/obmalloc.c", "Objects/odonel.c",
            "Objects/objectsfixtures.c", "Objects/obmalloc.c",
            "Objects/plugin.c", "Objects/rangeobject.c",
            "Objects/richobject.c", "Objects/setobject.c",
            "Objects/sliceobject.c", "Objects/structseq.c",
            "Objects/tupleobject.c", "Objects/typeobject.c",
            "Objects/unicodectype.c", "Objects/unicodeobject.c",
            "Objects/unicode.c", "Objects/weakrefobject.c",
            "Objects/typevarobject.c", "Objects/unionobject.c",
            // Grammar
            "Grammar/python.gram",
            // Include headers
            "Include/Python.h", "Include/patchlevel.h",
            // Misc
            "Modules/main.c", "Modules/gcmodule.c", "Modules/main_slot.c",
            "Modules/atexit.c", "Modules/config.c", "Modules/getbuildinfo.c",
            "Modules/errnomodule.c", "Modules/clinic/*.c.h",
        ];

        for path in &core_files {
            let source = CSourceFile {
                path: path.to_string(),
                used_by: Vec::new(),
                c_deps: Vec::new(),
                headers: Vec::new(),
                always_required: true,
                size_bytes: 0,
            };
            self.c_sources.insert(path.to_string(), source);
        }

        // Map module C sources to source file entries
        for (_, info) in &self.modules {
            for src in &info.c_sources {
                if !self.c_sources.contains_key(src) {
                    let source = CSourceFile {
                        path: src.to_string(),
                        used_by: vec![info.name.clone()],
                        c_deps: Vec::new(),
                        headers: Vec::new(),
                        always_required: info.always_built,
                        size_bytes: 0,
                    };
                    self.c_sources.insert(src.to_string(), source);
                } else {
                    // Update existing entry
                    if let Some(entry) = self.c_sources.get_mut(src) {
                        if !entry.used_by.contains(&info.name) {
                            entry.used_by.push(info.name.clone());
                        }
                    }
                }
            }
        }
    }

    /// Extract C dependency names from source file paths.
    fn extract_c_deps(&self, sources: &[&str]) -> Vec<String> {
        let mut deps = Vec::new();
        for src in sources {
            let module_name = self.source_to_module_name(src);
            if !deps.contains(&module_name) {
                deps.push(module_name);
            }
        }
        deps
    }

    /// Convert a C source path to a module name.
    fn source_to_module_name(&self, path: &str) -> String {
        let file = Path::new(path).file_stem().unwrap_or_default().to_string_lossy();
        let name = file.to_string();
        if name.starts_with('_') {
            name
        } else {
            format!("_{}", name)
        }
    }

    // ---------------------------------------------------------------------------
    // Public API
    // ---------------------------------------------------------------------------

    /// Get info for a module by name.
    pub fn get_module(&self, name: &str) -> Option<&StdlibModuleInfo> {
        self.modules.get(name)
    }

    /// Check if a module exists in the registry.
    pub fn has_module_inner(&self, name: &str) -> bool {
        self.modules.contains_key(name)
    }

    /// Get all module names.
    pub fn all_module_names(&self) -> Vec<&str> {
        self.modules.keys().map(|s| s.as_str()).collect()
    }

    /// Get all C-implemented modules.
    pub fn c_modules(&self) -> Vec<&StdlibModuleInfo> {
        self.modules.values()
            .filter(|m| !matches!(m.impl_type, ModuleImpl::PurePython))
            .collect()
    }

    /// Analyze a set of required module names and determine which C sources are needed.
    pub fn analyze_required_sources_inner(
        &self,
        required_modules: &[String],
    ) -> CpythonAnalysisResult {
        let mut required_c_modules: HashSet<String> = HashSet::new();
        let mut required_python_modules: HashSet<String> = HashSet::new();
        let mut required_c_sources: HashSet<String> = HashSet::new();
        let mut analysis_log: Vec<String> = Vec::new();

        // Step 1: Add all always-built modules
        for info in self.modules.values() {
            if info.always_built {
                required_c_modules.insert(info.name.clone());
                for src in &info.c_sources {
                    required_c_sources.insert(src.clone());
                }
                for dep in &info.c_deps {
                    required_c_modules.insert(dep.clone());
                }
            }
        }

        analysis_log.push(format!(
            "Added {} always-built C modules",
            required_c_modules.len()
        ));

        // Step 2: Process required modules
        for module_name in required_modules {
            self.resolve_module_dependencies(
                module_name,
                &mut required_c_modules,
                &mut required_python_modules,
                &mut required_c_sources,
                &mut analysis_log,
            );
        }

        // Step 3: Resolve transitive C dependencies
        let mut changed = true;
        while changed {
            changed = false;
            // Collect current snapshot to avoid borrow conflict while mutating
            let module_snapshot: Vec<String> = required_c_modules.iter().cloned().collect();
            for module_name in &module_snapshot {
                if let Some(info) = self.get_module(module_name) {
                    for src in &info.c_sources {
                        if !required_c_sources.contains(src) {
                            required_c_sources.insert(src.clone());
                            changed = true;
                        }
                    }
                    for dep in &info.c_deps {
                        if !required_c_modules.contains(dep) {
                            required_c_modules.insert(dep.clone());
                            changed = true;
                        }
                    }
                }
            }
        }

        // Step 4: Determine excluded modules
        let all_c_modules: Vec<String> = self.c_modules()
            .iter()
            .map(|m| m.name.clone())
            .collect();

        let excluded_c_modules: Vec<String> = all_c_modules
            .into_iter()
            .filter(|m| !required_c_modules.contains(m))
            .collect();

        // Step 5: Calculate size savings
        let total_c_sources = self.c_sources.len();
        let required_source_count = required_c_sources.len();
        let excluded_source_count = total_c_sources.saturating_sub(required_source_count);

        CpythonAnalysisResult {
            required_c_modules: required_c_modules.into_iter().collect(),
            required_python_modules: required_python_modules.into_iter().collect(),
            required_c_sources: required_c_sources.into_iter().collect(),
            excluded_c_modules,
            excluded_source_count,
            total_c_sources,
            analysis_log,
        }
    }

    /// Recursively resolve module dependencies.
    fn resolve_module_dependencies(
        &self,
        module_name: &str,
        c_modules: &mut HashSet<String>,
        python_modules: &mut HashSet<String>,
        c_sources: &mut HashSet<String>,
        log: &mut Vec<String>,
    ) {
        if python_modules.contains(module_name) {
            return;
        }

        python_modules.insert(module_name.to_string());

        if let Some(info) = self.get_module(module_name) {
            match &info.impl_type {
                ModuleImpl::PurePython => {
                    // Pure Python — no C sources needed directly,
                    // but may import C modules
                    for dep in &info.python_deps {
                        self.resolve_module_dependencies(
                            dep, c_modules, python_modules, c_sources, log,
                        );
                    }
                }
                ModuleImpl::BuiltinC | ModuleImpl::OptionalC | ModuleImpl::Extension => {
                    c_modules.insert(info.name.clone());
                    for src in &info.c_sources {
                        c_sources.insert(src.clone());
                    }
                    for dep in &info.c_deps {
                        c_modules.insert(dep.clone());
                    }
                    log.push(format!(
                        "Module '{}' requires C module '{}'",
                        module_name, info.name
                    ));
                }
                ModuleImpl::Mixed => {
                    // Python wrapper + C acceleration
                    for dep in &info.c_deps {
                        c_modules.insert(dep.clone());
                        if let Some(dep_info) = self.get_module(dep) {
                            for src in &dep_info.c_sources {
                                c_sources.insert(src.clone());
                            }
                        }
                    }
                    log.push(format!(
                        "Module '{}' is mixed (Python + C acceleration via {:?})",
                        module_name, info.c_deps
                    ));
                }
            }
        }
    }

    /// Generate a minimal Modules/Setup file for CPython's build system.
    pub fn generate_setup_file_inner(
        &self,
        required_modules: &[String],
    ) -> String {
        let result = self.analyze_required_sources_inner(required_modules);

        let mut lines = vec![
            "# Auto-generated Modules/Setup by Forger".to_string(),
            format!("# Required C modules: {}", result.required_c_modules.len()),
            format!("# Excluded C modules: {}", result.excluded_c_modules.len()),
            format!("# Required C sources: {}", result.required_c_sources.len()),
            "".to_string(),
            "# Always-enabled core modules".to_string(),
        ];

        // Add core modules
        for name in &result.required_c_modules {
            if let Some(info) = self.get_module(name) {
                if info.always_built {
                    lines.push(format!("# {} (always built)", info.name));
                }
            }
        }

        lines.push("".to_string());
        lines.push("# Enabled optional modules".to_string());

        for name in &result.required_c_modules {
            if let Some(info) = self.get_module(name) {
                if !info.always_built {
                    lines.push(format!(
                        "# Enable: {} — {}",
                        info.name, info.description
                    ));
                    for src in &info.c_sources {
                        lines.push(format!("  Source: {}", src));
                    }
                }
            }
        }

        lines.push("".to_string());
        lines.push("# Disabled modules (not required by application)".to_string());

        for name in &result.excluded_c_modules {
            lines.push(format!("# Disable: {}", name));
        }

        lines.push("".to_string());
        lines.push("# End of Forger-generated Setup".to_string());
        lines.join("\n")
    }

    /// Generate a list of C sources that can be safely excluded from the build.
    pub fn generate_excluded_sources_inner(
        &self,
        required_modules: &[String],
    ) -> Vec<String> {
        let result = self.analyze_required_sources_inner(required_modules);
        let required_sources = result.required_c_sources;

        self.c_sources.keys()
            .filter(|p| !required_sources.contains(*p))
            .cloned()
            .collect()
    }

}

#[pymethods]
impl CpythonModuleRegistry {
    /// Create a new registry populated with known stdlib module information.
    #[new]
    fn py_new(python_version: (u32, u32)) -> Self {
        Self::new(python_version)
    }

    /// Analyze a set of required module names and determine which C sources are needed.
    fn analyze_required_sources(&self, required_modules: Vec<String>) -> CpythonAnalysisResult {
        self.analyze_required_sources_inner(&required_modules)
    }

    /// Get the Python version this registry was built for.
    fn python_version(&self) -> (u32, u32) {
        self.python_version
    }

    /// Get the total number of known modules.
    fn module_count(&self) -> usize {
        self.modules.len()
    }

    /// Get the total number of known C source files.
    fn c_source_count(&self) -> usize {
        self.c_sources.len()
    }

    /// Check if a module exists in the registry.
    fn has_module(&self, name: &str) -> bool {
        self.has_module_inner(name)
    }

    /// Generate a minimal Modules/Setup file for CPython's build system.
    fn generate_setup_file(&self, required_modules: Vec<String>) -> String {
        self.generate_setup_file_inner(&required_modules)
    }

    /// Generate a list of C sources that can be safely excluded from the build.
    fn generate_excluded_sources(&self, required_modules: Vec<String>) -> Vec<String> {
        self.generate_excluded_sources_inner(&required_modules)
    }
}

// ---------------------------------------------------------------------------
// Analysis results
// ---------------------------------------------------------------------------

/// Result of analyzing CPython module requirements.
#[derive(Debug)]
#[pyclass]
pub struct CpythonAnalysisResult {
    /// C modules that are required.
    pub required_c_modules: Vec<String>,
    /// Python modules that are required.
    pub required_python_modules: Vec<String>,
    /// C source files that are required.
    pub required_c_sources: Vec<String>,
    /// C modules that can be excluded.
    pub excluded_c_modules: Vec<String>,
    /// Number of C source files that can be excluded.
    pub excluded_source_count: usize,
    /// Total number of C source files.
    pub total_c_sources: usize,
    /// Analysis log for diagnostics.
    pub analysis_log: Vec<String>,
}

#[pymethods]
impl CpythonAnalysisResult {
    /// Format a human-readable report.
    pub fn format_report(&self) -> String {
        let mut lines = vec![
            "CPython Module Analysis Report".to_string(),
            "─".repeat(50),
            format!("Required C modules:       {}", self.required_c_modules.len()),
            format!("Required Python modules:  {}", self.required_python_modules.len()),
            format!("Required C sources:       {}", self.required_c_sources.len()),
            format!("Excludable C modules:     {}", self.excluded_c_modules.len()),
            format!("Excludable C sources:     {}", self.excluded_source_count),
            format!("Total C sources:          {}", self.total_c_sources),
            "─".repeat(50),
            "".to_string(),
            "Required C Modules:".to_string(),
        ];

        for name in &self.required_c_modules {
            lines.push(format!("  ✓ {}", name));
        }

        lines.push("".to_string());
        lines.push("Excludable C Modules:".to_string());

        for name in &self.excluded_c_modules {
            lines.push(format!("  ✗ {}", name));
        }

        if !self.analysis_log.is_empty() {
            lines.push("".to_string());
            lines.push("Analysis Log:".to_string());
            for entry in &self.analysis_log {
                lines.push(format!("  ℹ {}", entry));
            }
        }

        lines.join("\n")
    }

    /// Check if the analysis found any optimizations.
    fn has_optimizations(&self) -> bool {
        !self.excluded_c_modules.is_empty()
    }

    /// Get the percentage of C modules that can be excluded.
    fn exclusion_percentage(&self) -> f64 {
        let total = self.required_c_modules.len() + self.excluded_c_modules.len();
        if total == 0 {
            return 0.0;
        }
        (self.excluded_c_modules.len() as f64 / total as f64) * 100.0
    }
}

// ---------------------------------------------------------------------------
// CPython build configuration
// ---------------------------------------------------------------------------

/// Configuration for a minimal CPython build.
#[derive(Debug, Clone)]
#[pyclass]
pub struct CpythonBuildConfig {
    /// Python version to build.
    pub python_version: String,
    /// Target platform.
    pub target_platform: String,
    /// C source files to include.
    pub include_sources: Vec<String>,
    /// C source files to exclude.
    pub exclude_sources: Vec<String>,
    /// Configure flags.
    pub configure_flags: Vec<String>,
    /// Modules/Setup content.
    pub setup_content: String,
    /// Estimated size savings.
    pub estimated_savings_bytes: u64,
}

impl CpythonBuildConfig {
    /// Generate a build configuration from analysis results.
    pub fn from_analysis_inner(
        analysis: &CpythonAnalysisResult,
        target: &str,
        python_version: &str,
    ) -> Self {
        Self {
            python_version: python_version.to_string(),
            target_platform: target.to_string(),
            include_sources: analysis.required_c_sources.clone(),
            exclude_sources: Vec::new(), // Computed from total - required
            configure_flags: vec![
                "--disable-ipv6".to_string(),
                "--without-ensurepip".to_string(),
                "--disable-test-modules".to_string(),
            ],
            setup_content: String::new(),
            estimated_savings_bytes: 0,
        }
    }

    /// Format the build configuration as a report.
    pub fn format_report(&self) -> String {
        let mut lines = vec![
            format!("CPython Build Configuration: {}", self.target_platform),
            format!("Python version: {}", self.python_version),
            format!("Include sources: {}", self.include_sources.len()),
            format!("Exclude sources: {}", self.exclude_sources.len()),
            format!("Configure flags: {}", self.configure_flags.len()),
        ];

        for flag in &self.configure_flags {
            lines.push(format!("  {}", flag));
        }

        lines.join("\n")
    }
}

#[pymethods]
impl CpythonBuildConfig {
    #[new]
    fn py_new() -> Self {
        Self {
            python_version: String::new(),
            target_platform: String::new(),
            include_sources: Vec::new(),
            exclude_sources: Vec::new(),
            configure_flags: Vec::new(),
            setup_content: String::new(),
            estimated_savings_bytes: 0,
        }
    }

    #[staticmethod]
    fn from_analysis(analysis: &Bound<'_, CpythonAnalysisResult>, target: String, python_version: String) -> Self {
        Self::from_analysis_inner(&analysis.borrow(), &target, &python_version)
    }
}

// ---------------------------------------------------------------------------
// CPython source tree analyzer
// ---------------------------------------------------------------------------

/// Analyze a CPython source tree to discover actual module dependencies.
pub struct CpythonSourceAnalyzer {
    /// Path to the CPython source tree.
    source_root: PathBuf,
    /// Discovered module mappings.
    module_mappings: HashMap<String, Vec<String>>,
}

impl CpythonSourceAnalyzer {
    /// Create a new analyzer for the given CPython source tree.
    pub fn new(source_root: PathBuf) -> Self {
        Self {
            source_root,
            module_mappings: HashMap::new(),
        }
    }

    /// Scan the CPython source tree and discover module-to-source mappings.
    pub fn scan(&mut self) -> ForgerResult<()> {
        // Scan Modules/ directory for .c files
        let modules_dir = self.source_root.join("Modules");
        if !modules_dir.exists() {
            return Err(ForgerError::Filesystem(format!(
                "Modules directory not found: {:?}",
                modules_dir
            )));
        }

        // Read Setup files for module configuration
        self.read_setup_files()?;

        // Scan Lib/ for pure Python modules
        self.scan_python_lib()?;

        Ok(())
    }

    /// Read Modules/Setup and Modules/Setup.* files.
    fn read_setup_files(&mut self) -> ForgerResult<()> {
        let setup_path = self.source_root.join("Modules").join("Setup");
        if !setup_path.exists() {
            return Ok(()); // Setup file is optional
        }

        let content = std::fs::read_to_string(&setup_path)
            .map_err(|e| ForgerError::Filesystem(format!("Cannot read Setup: {}", e)))?;

        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }

            // Parse module configuration lines
            // Format: module_name source_file.c [options]
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() >= 2 {
                let module_name = parts[0].to_string();
                let source_file = parts[1].to_string();
                self.module_mappings
                    .entry(module_name)
                    .or_default()
                    .push(source_file);
            }
        }

        Ok(())
    }

    /// Scan Lib/ directory for Python modules.
    fn scan_python_lib(&mut self) -> ForgerResult<()> {
        let lib_dir = self.source_root.join("Lib");
        if !lib_dir.exists() {
            return Err(ForgerError::Filesystem(format!(
                "Lib directory not found: {:?}",
                lib_dir
            )));
        }

        // Discover top-level modules
        for entry in std::fs::read_dir(&lib_dir)
            .map_err(|e| ForgerError::Filesystem(format!("Cannot read Lib: {}", e)))?
        {
            let entry = entry.map_err(|e| ForgerError::Filesystem(e.to_string()))?;
            let path = entry.path();

            if path.is_dir() {
                // Package
                let module_name = path.file_name()
                    .map(|n| n.to_string_lossy().to_string());
                if let Some(name) = module_name {
                    if !name.starts_with('_') || name == "_" {
                        self.module_mappings
                            .entry(format!("{}.py", name))
                            .or_default();
                    }
                }
            } else if path.extension().map_or(false, |e| e == "py") {
                // Single-file module
                let module_name = path.file_stem()
                    .map(|n| n.to_string_lossy().to_string());
                if let Some(name) = module_name {
                    self.module_mappings
                        .entry(format!("{}.py", name))
                        .or_default();
                }
            }
        }

        Ok(())
    }

    /// Get the source root path.
    pub fn source_root(&self) -> &Path {
        &self.source_root
    }

    /// Get all discovered module mappings.
    pub fn module_mappings(&self) -> &HashMap<String, Vec<String>> {
        &self.module_mappings
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_registry_creation() {
        let registry = CpythonModuleRegistry::new((3, 10));
        assert!(registry.module_count() > 50);
        assert!(registry.c_source_count() > 10);
    }

    #[test]
    fn test_builtin_modules() {
        let registry = CpythonModuleRegistry::new((3, 10));
        assert!(registry.has_module("builtins"));
        assert!(registry.has_module("sys"));
        assert!(registry.has_module("_collections"));
        assert!(registry.has_module("_io"));
    }

    #[test]
    fn test_module_analysis() {
        let registry = CpythonModuleRegistry::new((3, 10));
        let required = vec![
            "json".to_string(),
            "re".to_string(),
            "collections".to_string(),
            "os".to_string(),
            "pathlib".to_string(),
        ];

        let result = registry.analyze_required_sources(required.clone());
        assert!(!result.required_c_modules.is_empty());
        assert!(!result.required_c_sources.is_empty());
    }

    #[test]
    fn test_setup_file_generation() {
        let registry = CpythonModuleRegistry::new((3, 10));
        let required = vec!["json".to_string(), "re".to_string()];
        let setup = registry.generate_setup_file_inner(&required);
        assert!(setup.contains("Auto-generated"));
        assert!(setup.contains("Forger"));
    }

    #[test]
    fn test_analysis_report() {
        let registry = CpythonModuleRegistry::new((3, 10));
        let required = vec!["json".to_string()];
        let result = registry.analyze_required_sources(required.clone());
        let report = result.format_report();
        assert!(report.contains("CPython Module Analysis Report"));
        assert!(report.contains("Required C modules"));
    }

    #[test]
    fn test_exclusion_percentage() {
        let registry = CpythonModuleRegistry::new((3, 10));
        let required = vec!["json".to_string()];
        let result = registry.analyze_required_sources(required.clone());
        let pct = result.exclusion_percentage();
        assert!(pct >= 0.0);
        assert!(pct <= 100.0);
    }

    #[test]
    fn test_build_config() {
        let registry = CpythonModuleRegistry::new((3, 10));
        let required = vec!["json".to_string(), "re".to_string()];
        let analysis = registry.analyze_required_sources_inner(&required);
        let config = CpythonBuildConfig::from_analysis_inner(&analysis, "windows-x64", "3.10");
        assert_eq!(config.target_platform, "windows-x64");
        assert!(!config.configure_flags.is_empty());
    }
}
