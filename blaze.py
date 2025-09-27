import tkinter as tk
from tkinter import filedialog, StringVar, BooleanVar, IntVar, BOTH, LEFT, RIGHT, TOP, BOTTOM
from tkinter import Frame, Text, Scrollbar, Y, ttk, messagebox, Menu, PhotoImage
import logging
import threading
import os
import sys
from pathlib import Path
import core as fylex


# ---------------- Enhanced Logging Handler ----------------
class TextHandler(logging.Handler):
    """Enhanced log handler with colored output."""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.setup_tags()

    def setup_tags(self):
        """Configure text tags for different log levels."""
        self.text_widget.tag_configure("INFO", foreground="#00FF00")
        self.text_widget.tag_configure("WARNING", foreground="#FFB347")
        self.text_widget.tag_configure("ERROR", foreground="#FF6B6B")
        self.text_widget.tag_configure("DEBUG", foreground="#87CEEB")

    def emit(self, record):
        msg = self.format(record)
        level = record.levelname
        
        def append():
            self.text_widget.configure(state="normal")
            start_pos = self.text_widget.index("end-1c")
            self.text_widget.insert("end", msg + "\n")
            end_pos = self.text_widget.index("end-1c")
            
            # Apply color tag based on log level
            if level in ["INFO", "WARNING", "ERROR", "DEBUG"]:
                self.text_widget.tag_add(level, start_pos, end_pos)
            
            self.text_widget.see("end")
            self.text_widget.configure(state="disabled")
        
        # Thread-safe GUI updates
        self.text_widget.after(0, append)


# ---------------- Progress Dialog ----------------
class ProgressDialog:
    def __init__(self, parent, title="Processing..."):
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("400x150")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Center the dialog
        self.dialog.geometry("+%d+%d" % (parent.winfo_rootx() + 50, parent.winfo_rooty() + 50))
        
        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.dialog, 
            variable=self.progress_var,
            mode='indeterminate'
        )
        self.progress_bar.pack(pady=20, padx=20, fill='x')
        
        # Status label
        self.status_var = StringVar(value="Initializing...")
        self.status_label = ttk.Label(self.dialog, textvariable=self.status_var)
        self.status_label.pack(pady=10)
        
        # Cancel button
        self.cancelled = False
        self.cancel_btn = ttk.Button(
            self.dialog, 
            text="Cancel", 
            command=self.cancel
        )
        self.cancel_btn.pack(pady=10)
        
        self.progress_bar.start(10)
    
    def update_status(self, status):
        self.status_var.set(status)
        self.dialog.update()
    
    def cancel(self):
        self.cancelled = True
        self.close()
    
    def close(self):
        self.progress_bar.stop()
        self.dialog.destroy()


# ---------------- Enhanced GUI Class ----------------
class FylexGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.setup_window()
        self.setup_variables()
        self.setup_styles()
        self.create_menu()
        self.create_widgets()
        self.setup_logging()
        
    def setup_window(self):
        """Configure main window."""
        def resource_path(relative_path):
            try:
                base_path = sys._MEIPASS
            except Exception:
                base_path = os.path.abspath(".")
            return os.path.join(base_path, relative_path)

        self.root.title("Blazefox 2.3 - Advanced File Manager")
        self.root.state('zoomed') if os.name == 'nt' else self.root.attributes('-zoomed', True)
        
        # Set minimum size
        self.root.minsize(1000, 600)
        
        # Configure icon (if available)
        try:
            icon = PhotoImage(file=resource_path("assets/blazefox.png"))
            self.root.iconphoto(True, icon)
        except Exception as e:
            pass
                        
        # Configure closing behavior
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
    def setup_variables(self):
        """Initialize all GUI variables."""
        self.src_var = StringVar()
        self.dest_var = StringVar()
        self.summary_var = StringVar()
        self.inc_regex_var = StringVar()
        self.exc_regex_var = StringVar()
        self.inc_glob_var = StringVar()
        self.exc_glob_var = StringVar()
        self.resolve_var = StringVar(value="rename")
        self.algo_var = StringVar(value=fylex.FylexConfig.DEFAULT_HASH_ALGO)
        self.chunk_var = IntVar(value=fylex.FylexConfig.DEFAULT_CHUNK_SIZE)
        self.pid_var = StringVar()
        self.action_var = StringVar(value="copy")
        
        # Boolean flags
        self.dry_run_var = BooleanVar(value=False)
        self.interactive_var = BooleanVar(value=False)
        self.verify_var = BooleanVar(value=False)
        self.preserve_meta_var = BooleanVar(value=True)
        self.recurse_var = BooleanVar(value=False)
        self.recursive_check_var = BooleanVar(value=False)
        self.has_ext_var = BooleanVar(value=False)
        self.no_create_var = BooleanVar(value=False)
        self.force_var = BooleanVar(value=False)
        
        # Status variables
        self.status_var = StringVar(value="Ready")
        self.operation_running = False

    def setup_styles(self):
        """Configure modern styling."""
        style = ttk.Style()
        
        # Configure themes
        try:
            style.theme_use('clam')  # Modern theme
        except:
            pass
            
        # Custom styles
        style.configure('Title.TLabel', font=('Arial', 14, 'bold'))
        style.configure('Heading.TLabel', font=('Arial', 12, 'bold'))
        style.configure('Action.TButton', font=('Arial', 12, 'bold'))
        
        # Configure colors
        self.colors = {
            'primary': '#2E86AB',
            'secondary': '#A23B72',
            'success': '#F18F01',
            'danger': '#C73E1D',
            'light': '#F5F5F5',
            'dark': '#2C3E50'
        }

    def create_menu(self):
        """Create application menu bar."""
        menubar = Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Save Configuration", command=self.save_config)
        file_menu.add_command(label="Load Configuration", command=self.load_config)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing)
        
        # Tools menu
        tools_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        tools_menu.add_command(label="Clear Log", command=self.clear_log)
        tools_menu.add_command(label="Export Log", command=self.export_log)
        tools_menu.add_command(label="Validate Paths", command=self.validate_paths)
        
        # Help menu
        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
        help_menu.add_command(label="Documentation", command=self.show_help)

    def create_widgets(self):
        """Create and layout all widgets."""
        # Main container with padding
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill=BOTH, expand=True)
        
        # Create paned window for resizable layout
        paned = ttk.PanedWindow(main_container, orient='horizontal')
        paned.pack(fill=BOTH, expand=True, pady=5)
        
        # Left panel - Controls
        self.create_control_panel(paned)
        
        # Right panel - Console and status
        self.create_console_panel(paned)
        
        # Status bar
        self.create_status_bar()

    def create_control_panel(self, parent):
        """Create the left control panel."""
        left_frame = ttk.Frame(parent, padding="10")
        parent.add(left_frame, weight=1)
        
        # Title
        title_label = ttk.Label(
            left_frame, 
            text="Blazefox File Operations", 
            style='Title.TLabel'
        )
        title_label.pack(pady=(0, 20))
        
        # Action selection
        self.create_action_selector(left_frame)
        
        # Dynamic forms container
        self.forms_container = ttk.Frame(left_frame)
        self.forms_container.pack(fill=BOTH, expand=True, pady=10)
        
        # Create all forms
        self.create_copy_move_form()
        self.create_undo_redo_form()
        
        # Control buttons
        self.create_control_buttons(left_frame)
        
        # Setup form switching
        self.setup_form_switching()

    def create_action_selector(self, parent):
        """Create action selection widget."""
        action_frame = ttk.LabelFrame(parent, text="Operation", padding="10")
        action_frame.pack(fill='x', pady=(0, 10))
        
        actions = [
            ("Copy Files", "copy"),
            ("Move Files", "move"), 
            ("Undo Operation", "undo"),
            ("Redo Operation", "redo")
        ]
        
        for text, value in actions:
            rb = ttk.Radiobutton(
                action_frame, 
                text=text, 
                variable=self.action_var, 
                value=value
            )
            rb.pack(anchor='w', pady=2)

    def create_copy_move_form(self):
        """Create copy/move operation form."""
        self.copy_move_frame = ttk.Frame(self.forms_container)
        
        # Path configuration
        paths_frame = ttk.LabelFrame(self.copy_move_frame, text="Paths", padding="0")
        paths_frame.pack(fill='x', pady=(0, 10))
        
        self.add_path_row(paths_frame, 0, "Source Path:", self.src_var, folder=True)
        self.add_path_row(paths_frame, 1, "Destination Path:", self.dest_var, folder=True)
        self.add_path_row(paths_frame, 2, "Summary File:", self.summary_var, file=True)
        
        # Filters
        filters_frame = ttk.LabelFrame(self.copy_move_frame, text="Filters", padding="10")
        filters_frame.pack(fill='x', pady=(0, 10))
        
        self.add_entry_row(filters_frame, 0, "Include Regex:", self.inc_regex_var)
        self.add_entry_row(filters_frame, 1, "Exclude Regex:", self.exc_regex_var)
        self.add_entry_row(filters_frame, 2, "Include Glob:", self.inc_glob_var)
        self.add_entry_row(filters_frame, 3, "Exclude Glob:", self.exc_glob_var)
        
        # Settings
        settings_frame = ttk.LabelFrame(self.copy_move_frame, text="Settings", padding="10")
        settings_frame.pack(fill='x', pady=(0, 10))
        
        # Dropdowns
        self.add_dropdown_row(settings_frame, 0, "Conflict Resolution:", 
                             self.resolve_var, fylex.FylexConfig.ON_CONFLICT_MODES)
        self.add_dropdown_row(settings_frame, 1, "Hash Algorithm:", 
                             self.algo_var, ["xxhash", "blake3", "md5", "sha256", "sha512"])
        self.add_entry_row(settings_frame, 2, "Chunk Size (bytes):", self.chunk_var)
        
        # Options checkboxes
        options_frame = ttk.LabelFrame(self.copy_move_frame, text="Options", padding="10")
        options_frame.pack(fill='x', pady=(0, 10))
        
        options = [
            ("Dry Run (Preview Only)", self.dry_run_var),
            ("Interactive Mode", self.interactive_var),
            ("Verify After Operation", self.verify_var),
            ("Preserve Metadata", self.preserve_meta_var),
            ("Recurse Subdirectories", self.recurse_var),
            ("Recursive Duplicate Check", self.recursive_check_var),
            ("Match by File Extension", self.has_ext_var),
            ("Don't Create Destination", self.no_create_var)
        ]
        
        # Arrange checkboxes in two columns
        for i, (text, var) in enumerate(options):
            row, col = divmod(i, 2)
            cb = ttk.Checkbutton(options_frame, text=text, variable=var)
            cb.grid(row=row, column=col, sticky='w', padx=5, pady=2)

    def create_undo_redo_form(self):
        """Create undo/redo operation form."""
        self.undo_redo_frame = ttk.Frame(self.forms_container)
        
        # Process ID
        pid_frame = ttk.LabelFrame(self.undo_redo_frame, text="Process Information", padding="10")
        pid_frame.pack(fill='x', pady=(0, 10))
        
        self.add_entry_row(pid_frame, 0, "Process ID:", self.pid_var)
        self.add_path_row(pid_frame, 1, "Summary File:", self.summary_var, file=True)
        
        # Options
        options_frame = ttk.LabelFrame(self.undo_redo_frame, text="Options", padding="10")
        options_frame.pack(fill='x', pady=(0, 10))
        
        ttk.Checkbutton(options_frame, text="Force Operation", variable=self.force_var).pack(anchor='w')
        ttk.Checkbutton(options_frame, text="Dry Run", variable=self.dry_run_var).pack(anchor='w')

    def add_path_row(self, parent, row, label, var, folder=False, file=False):
        """Add a path input row with browse button."""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=5, pady=5)
        
        path_frame = ttk.Frame(parent)
        path_frame.grid(row=row, column=1, sticky='ew', padx=5, pady=5)
        parent.grid_columnconfigure(1, weight=1)
        
        entry = ttk.Entry(path_frame, textvariable=var, width=50)
        entry.pack(side=LEFT, fill='x', expand=True, padx=(0, 5))
        
        if folder:
            btn = ttk.Button(path_frame, text="Browse", 
                           command=lambda: var.set(filedialog.askdirectory()))
        elif file:
            btn = ttk.Button(path_frame, text="Browse", 
                           command=lambda: var.set(filedialog.asksaveasfilename()))
        else:
            return
            
        btn.pack(side=RIGHT)

    def add_entry_row(self, parent, row, label, var):
        """Add a simple entry row."""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=5, pady=5)
        entry = ttk.Entry(parent, textvariable=var, width=50)
        entry.grid(row=row, column=1, sticky='ew', padx=5, pady=5)
        parent.grid_columnconfigure(1, weight=1)

    def add_dropdown_row(self, parent, row, label, var, options):
        """Add a dropdown row."""
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=5, pady=5)
        combo = ttk.Combobox(parent, textvariable=var, values=options, state='readonly')
        combo.grid(row=row, column=1, sticky='ew', padx=5, pady=5)
        parent.grid_columnconfigure(1, weight=1)

    def create_control_buttons(self, parent):
        """Create control buttons."""
        button_frame = ttk.Frame(parent)
        button_frame.pack(side=BOTTOM, fill='x', pady=(20, 0))
        
        # Run button
        self.run_button = ttk.Button(
            button_frame, 
            text="Execute Operation", 
            command=self.run_operation_threaded,
            style='Action.TButton'
        )
        self.run_button.pack(side=LEFT, padx=(0, 10))
        
        # Validate button
        validate_btn = ttk.Button(
            button_frame, 
            text="Validate", 
            command=self.validate_inputs
        )
        validate_btn.pack(side=LEFT, padx=(0, 10))
        
        # Reset button
        reset_btn = ttk.Button(
            button_frame, 
            text="Reset", 
            command=self.reset_form
        )
        reset_btn.pack(side=LEFT)

    def create_console_panel(self, parent):
        """Create the right console panel."""
        right_frame = ttk.Frame(parent, padding="10")
        parent.add(right_frame, weight=1)
        
        # Console title
        console_title = ttk.Label(
            right_frame, 
            text="Operation Log", 
            style='Heading.TLabel'
        )
        console_title.pack(anchor='w', pady=(0, 10))
        
        # Console frame with scrollbars
        console_frame = ttk.Frame(right_frame)
        console_frame.pack(fill=BOTH, expand=True)
        
        # Text widget with scrollbars
        self.log_text = Text(
            console_frame, 
            state="disabled", 
            wrap="word", 
            font=("Consolas", 10),
            bg="#1e1e1e", 
            fg="#ffffff",
            insertbackground="white",
            selectbackground="#3399ff"
        )
        
        # Vertical scrollbar
        v_scrollbar = ttk.Scrollbar(console_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=v_scrollbar.set)
        
        # Horizontal scrollbar
        h_scrollbar = ttk.Scrollbar(console_frame, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(xscrollcommand=h_scrollbar.set)
        
        # Pack scrollbars and text
        v_scrollbar.pack(side=RIGHT, fill=Y)
        h_scrollbar.pack(side=BOTTOM, fill='x')
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        
        # Console controls
        console_controls = ttk.Frame(right_frame)
        console_controls.pack(fill='x', pady=(10, 0))
        
        ttk.Button(console_controls, text="Clear Log", command=self.clear_log).pack(side=LEFT, padx=(0, 5))
        ttk.Button(console_controls, text="Export Log", command=self.export_log).pack(side=LEFT)

    def create_status_bar(self):
        """Create status bar."""
        status_frame = ttk.Frame(self.root)
        status_frame.pack(side=BOTTOM, fill='x')
        
        # Status label
        status_label = ttk.Label(
            status_frame, 
            textvariable=self.status_var, 
            relief='sunken', 
            anchor='w'
        )
        status_label.pack(side=LEFT, fill='x', expand=True, padx=2, pady=2)
        
        # Progress bar (initially hidden)
        self.progress_var = tk.DoubleVar()
        self.status_progress = ttk.Progressbar(
            status_frame, 
            variable=self.progress_var,
            mode='indeterminate'
        )

    def setup_form_switching(self):
        """Setup dynamic form switching."""
        def update_form(*args):
            # Hide all forms
            self.copy_move_frame.pack_forget()
            self.undo_redo_frame.pack_forget()
            
            # Show appropriate form
            if self.action_var.get() in ("copy", "move"):
                self.copy_move_frame.pack(fill=BOTH, expand=True)
            elif self.action_var.get() in ("undo", "redo"):
                self.undo_redo_frame.pack(fill=BOTH, expand=True)
        
        self.action_var.trace_add("write", update_form)
        update_form()  # Initial setup

    def setup_logging(self):
        """Setup enhanced logging."""
        # Create and configure handler
        self.handler = TextHandler(self.log_text)
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", 
                                    datefmt="%H:%M:%S")
        self.handler.setFormatter(formatter)
        
        # Configure logger
        logger = logging.getLogger()
        logger.addHandler(self.handler)
        logger.setLevel(logging.INFO)
        
        # Welcome message
        logging.info("Blazefox GUI initialized successfully")
        logging.info("Ready for file operations")

    def run_operation_threaded(self):
        """Run operation in separate thread."""
        if self.operation_running:
            messagebox.showwarning("Operation in Progress", "Another operation is currently running.")
            return
            
        if not self.validate_inputs():
            return
            
        # Disable run button
        self.operation_running = True
        self.run_button.config(state='disabled', text='Running...')
        self.status_var.set("Operation in progress...")
        self.status_progress.pack(side=RIGHT, padx=(5, 2), pady=2)
        self.status_progress.start()
        
        # Run in thread
        thread = threading.Thread(target=self.run_operation)
        thread.daemon = True
        thread.start()

    def run_operation(self):
        """Execute the selected operation."""
        try:
            action = self.action_var.get()
            
            if action == "copy":
                fylex.filecopy(
                    self.src_var.get(), 
                    self.dest_var.get(),
                    resolve=self.resolve_var.get(), 
                    algo=self.algo_var.get(),
                    chunk_size=self.chunk_var.get(), 
                    dry_run=self.dry_run_var.get(),
                    preserve_meta=self.preserve_meta_var.get(), 
                    verify=self.verify_var.get(),
                    recurse=self.recurse_var.get(), 
                    recursive_check=self.recursive_check_var.get(),
                    has_extension=self.has_ext_var.get(), 
                    no_create=self.no_create_var.get(),
                    match_regex=self.inc_regex_var.get() or None, 
                    exclude_regex=self.exc_regex_var.get() or None,
                    match_glob=self.inc_glob_var.get() or None, 
                    exclude_glob=self.exc_glob_var.get() or None,
                    summary=self.summary_var.get() or None
                )
                
            elif action == "move":
                fylex.filemove(
                    self.src_var.get(), 
                    self.dest_var.get(),
                    resolve=self.resolve_var.get(), 
                    algo=self.algo_var.get(),
                    chunk_size=self.chunk_var.get(), 
                    dry_run=self.dry_run_var.get(),
                    preserve_meta=self.preserve_meta_var.get(), 
                    verify=self.verify_var.get(),
                    recurse=self.recurse_var.get(), 
                    recursive_check=self.recursive_check_var.get(),
                    has_extension=self.has_ext_var.get(), 
                    no_create=self.no_create_var.get(),
                    match_regex=self.inc_regex_var.get() or None, 
                    exclude_regex=self.exc_regex_var.get() or None,
                    match_glob=self.inc_glob_var.get() or None, 
                    exclude_glob=self.exc_glob_var.get() or None,
                    summary=self.summary_var.get() or None
                )
                
            elif action == "undo":
                fylex.undo(self.pid_var.get(), force=self.force_var.get(), verbose=True, summary=self.summary_var.get(), dry_run=self.dry_run_var.get())
                
            elif action == "redo":
                fylex.redo(self.pid_var.get(), force=self.force_var.get(), verbose=True, summary=self.summary_var.get(), dry_run=self.dry_run_var.get())
                
            # Success message
            self.root.after(0, lambda: self.operation_completed(True))
            
        except Exception as e:
            logging.error(f"Operation failed: {e}")
            self.root.after(0, lambda: self.operation_completed(False))

    def operation_completed(self, success):
        """Handle operation completion."""
        self.operation_running = False
        self.run_button.config(state='normal', text='Execute Operation')
        self.status_progress.stop()
        self.status_progress.pack_forget()
        
        if success:
            self.status_var.set("Operation completed successfully")
            logging.info("Operation completed successfully")
        else:
            self.status_var.set("Operation failed - check log for details")

    def validate_inputs(self):
        """Validate user inputs."""
        action = self.action_var.get()
        
        if action in ("copy", "move"):
            if not self.src_var.get():
                messagebox.showerror("Validation Error", "Source path is required")
                return False
            if not self.dest_var.get():
                messagebox.showerror("Validation Error", "Destination path is required")
                return False
            if not os.path.exists(self.src_var.get()):
                messagebox.showerror("Validation Error", "Source path does not exist")
                return False
                
        elif action in ("undo", "redo"):
            if not self.pid_var.get():
                messagebox.showerror("Validation Error", "Process ID is required")
                return False
                
        return True

    def validate_paths(self):
        """Validate all path inputs."""
        issues = []
        
        # Check source path
        if self.src_var.get() and not os.path.exists(self.src_var.get()):
            issues.append("Source path does not exist")
            
        # Check destination path parent
        dest_path = self.dest_var.get()
        if dest_path:
            parent = os.path.dirname(dest_path)
            if parent and not os.path.exists(parent):
                issues.append("Destination parent directory does not exist")
        
        if issues:
            messagebox.showwarning("Path Validation", "\n".join(issues))
        else:
            messagebox.showinfo("Path Validation", "All paths are valid!")

    def reset_form(self):
        """Reset form to default values."""
        if messagebox.askyesno("Reset Form", "Are you sure you want to reset all fields?"):
            # Reset all variables
            for var in [self.src_var, self.dest_var, self.summary_var, 
                       self.inc_regex_var, self.exc_regex_var, self.inc_glob_var, 
                       self.exc_glob_var, self.pid_var]:
                var.set("")
                
            # Reset to defaults
            self.resolve_var.set("rename")
            self.algo_var.set(fylex.FylexConfig.DEFAULT_HASH_ALGO)
            self.chunk_var.set(fylex.FylexConfig.DEFAULT_CHUNK_SIZE)
            self.action_var.set("copy")
            
            # Reset boolean flags
            for var in [self.dry_run_var, self.interactive_var, self.verify_var, 
                       self.recurse_var, self.recursive_check_var, self.has_ext_var, 
                       self.no_create_var, self.force_var]:
                var.set(False)
                
            self.preserve_meta_var.set(True)
            
            logging.info("Form reset to default values")

    def clear_log(self):
        """Clear the log console."""
        self.log_text.configure(state="normal")
        self.log_text.delete(1.0, 'end')
        self.log_text.configure(state="disabled")
        logging.info("Log cleared")

    def export_log(self):
        """Export log to file."""
        filename = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.log_text.get(1.0, 'end'))
                messagebox.showinfo("Export Successful", f"Log exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Failed", f"Failed to export log: {e}")

    def save_config(self):
        """Save current configuration."""
        # Implementation for saving configuration
        messagebox.showinfo("Save Config", "Configuration save feature coming soon!")

    def load_config(self):
        """Load configuration from file."""
        # Implementation for loading configuration
        messagebox.showinfo("Load Config", "Configuration load feature coming soon!")

    def show_about(self):
        """Show about dialog."""
        about_text = """Blazefox - Advanced File Manager

Version: 2.2
A powerful file management tool with advanced features:

• Fast file copying and moving with verification
• Duplicate detection and handling
• Regex and glob pattern filtering  
• Hash-based integrity checking
• Undo/Redo operations with transaction logs
• Metadata preservation
• Multi-threaded operations

Built with Python and Tkinter
Enhanced GUI with modern styling and improved usability
Report bugs at: sivaprasad.off@gmail.com

© 2025 Sivaprasad Murali"""
        
        messagebox.showinfo("About Blazefox", about_text)

    def show_help(self):
        """Show help documentation."""
        help_text = """Blazefox Help & Documentation

OPERATIONS:
• Copy: Duplicate files from source to destination
• Move: Transfer files from source to destination  
• Undo: Reverse a previous operation using Process ID
• Redo: Re-execute a previously undone operation

PATH SETTINGS:
• Source Path: Directory containing files to process
• Destination Path: Target directory for files
• Summary File: Optional transaction log file

FILTERS:
• Include/Exclude Regex: Pattern matching for filenames
• Include/Exclude Glob: Wildcard pattern matching
• Example regex: \.jpg$|\.png$ (match JPG or PNG files)
• Example glob: *.pdf (match all PDF files)

CONFLICT RESOLUTION:
• rename: Add suffix to duplicate files
• skip: Skip files that already exist
• overwrite: Replace existing files
• prompt: Ask user for each conflict

HASH ALGORITHMS:
• xxhash: Fastest, good for most use cases
• blake3: Modern, secure, fast
• md5: Legacy, fast but less secure
• sha256/sha512: Secure but slower

OPTIONS:
• Dry Run: Preview operations without executing
• Interactive: Prompt for confirmations
• Verify: Check file integrity after operations
• Preserve Metadata: Keep file timestamps and permissions
• Recurse: Include subdirectories
• Recursive Check: Check for duplicates in subdirs

KEYBOARD SHORTCUTS:
• Ctrl+R: Execute operation
• Ctrl+L: Clear log
• Ctrl+S: Save configuration
• Ctrl+O: Load configuration
• F1: Show this help

For more information, visit the Blazefox documentation."""
        
        # Create help window
        help_window = tk.Toplevel(self.root)
        help_window.title("Blazefox Help")
        help_window.geometry("600x500")
        help_window.transient(self.root)
        
        # Help text widget with scrollbar
        help_frame = ttk.Frame(help_window, padding="10")
        help_frame.pack(fill=BOTH, expand=True)
        
        help_text_widget = Text(
            help_frame, 
            wrap="word", 
            font=("Arial", 10),
            state="normal"
        )
        help_scrollbar = ttk.Scrollbar(help_frame, command=help_text_widget.yview)
        help_text_widget.configure(yscrollcommand=help_scrollbar.set)
        
        help_scrollbar.pack(side=RIGHT, fill=Y)
        help_text_widget.pack(side=LEFT, fill=BOTH, expand=True)
        
        help_text_widget.insert(1.0, help_text)
        help_text_widget.configure(state="disabled")
        
        # Close button
        ttk.Button(
            help_window, 
            text="Close", 
            command=help_window.destroy
        ).pack(pady=10)

    def setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts."""
        self.root.bind('<Control-r>', lambda e: self.run_operation_threaded())
        self.root.bind('<Control-l>', lambda e: self.clear_log())
        self.root.bind('<Control-s>', lambda e: self.save_config())
        self.root.bind('<Control-o>', lambda e: self.load_config())
        self.root.bind('<F1>', lambda e: self.show_help())
        self.root.bind('<Control-q>', lambda e: self.on_closing())

    def on_closing(self):
        """Handle application closing."""
        if self.operation_running:
            if not messagebox.askyesno("Operation in Progress", 
                                     "An operation is currently running. Force quit?"):
                return
        
        # Save window state, cleanup, etc.
        self.root.quit()
        self.root.destroy()

    def run(self):
        """Start the GUI application."""
        self.setup_keyboard_shortcuts()
        
        # Set initial status
        self.status_var.set("Ready - Select an operation to begin")
        
        # Center window on screen
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (width // 2)
        y = (self.root.winfo_screenheight() // 2) - (height // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        
        # Start main loop
        self.root.mainloop()


# ---------------- Configuration Management ----------------
class ConfigManager:
    """Handle saving and loading of GUI configurations."""
    
    def __init__(self, gui):
        self.gui = gui
        self.config_file = "fylex_config.json"
    
    def save_config(self):
        """Save current GUI state to configuration file."""
        try:
            import json
            
            config = {
                'action': self.gui.action_var.get(),
                'source_path': self.gui.src_var.get(),
                'dest_path': self.gui.dest_var.get(),
                'summary_path': self.gui.summary_var.get(),
                'include_regex': self.gui.inc_regex_var.get(),
                'exclude_regex': self.gui.exc_regex_var.get(),
                'include_glob': self.gui.inc_glob_var.get(),
                'exclude_glob': self.gui.exc_glob_var.get(),
                'resolve_mode': self.gui.resolve_var.get(),
                'hash_algo': self.gui.algo_var.get(),
                'chunk_size': self.gui.chunk_var.get(),
                'process_id': self.gui.pid_var.get(),
                'flags': {
                    'dry_run': self.gui.dry_run_var.get(),
                    'interactive': self.gui.interactive_var.get(),
                    'verify': self.gui.verify_var.get(),
                    'preserve_meta': self.gui.preserve_meta_var.get(),
                    'recurse': self.gui.recurse_var.get(),
                    'recursive_check': self.gui.recursive_check_var.get(),
                    'has_extension': self.gui.has_ext_var.get(),
                    'no_create': self.gui.no_create_var.get(),
                    'force': self.gui.force_var.get()
                }
            }
            
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
                
            return True
            
        except Exception as e:
            logging.error(f"Failed to save configuration: {e}")
            return False
    
    def load_config(self):
        """Load configuration from file."""
        try:
            import json
            
            if not os.path.exists(self.config_file):
                return False
                
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            
            # Apply configuration
            self.gui.action_var.set(config.get('action', 'copy'))
            self.gui.src_var.set(config.get('source_path', ''))
            self.gui.dest_var.set(config.get('dest_path', ''))
            self.gui.summary_var.set(config.get('summary_path', ''))
            self.gui.inc_regex_var.set(config.get('include_regex', ''))
            self.gui.exc_regex_var.set(config.get('exclude_regex', ''))
            self.gui.inc_glob_var.set(config.get('include_glob', ''))
            self.gui.exc_glob_var.set(config.get('exclude_glob', ''))
            self.gui.resolve_var.set(config.get('resolve_mode', 'rename'))
            self.gui.algo_var.set(config.get('hash_algo', 'xxhash'))
            self.gui.chunk_var.set(config.get('chunk_size', 1048576))
            self.gui.pid_var.set(config.get('process_id', ''))
            
            # Apply flags
            flags = config.get('flags', {})
            self.gui.dry_run_var.set(flags.get('dry_run', False))
            self.gui.interactive_var.set(flags.get('interactive', False))
            self.gui.verify_var.set(flags.get('verify', False))
            self.gui.preserve_meta_var.set(flags.get('preserve_meta', True))
            self.gui.recurse_var.set(flags.get('recurse', False))
            self.gui.recursive_check_var.set(flags.get('recursive_check', False))
            self.gui.has_ext_var.set(flags.get('has_extension', False))
            self.gui.no_create_var.set(flags.get('no_create', False))
            self.gui.force_var.set(flags.get('force', False))
            
            return True
            
        except Exception as e:
            logging.error(f"Failed to load configuration: {e}")
            return False


# ---------------- Enhanced Start Function ----------------
def start_gui():
    """Initialize and start the enhanced Blazefox GUI."""
    try:
        app = FylexGUI()
        
        # Initialize configuration manager
        config_manager = ConfigManager(app)
        
        # Override save/load methods
        app.save_config = lambda: config_manager.save_config() and \
                                messagebox.showinfo("Success", "Configuration saved successfully!")
        app.load_config = lambda: config_manager.load_config() and \
                                messagebox.showinfo("Success", "Configuration loaded successfully!") or \
                                messagebox.showwarning("Load Failed", "No configuration file found or failed to load")
        
        # Auto-load configuration on startup
        try:
            config_manager.load_config()
        except:
            pass  # Ignore errors on startup
        
        # Start the application
        app.run()
        
    except Exception as e:
        print(f"Failed to start Blazefox GUI: {e}")
        import traceback
        traceback.print_exc()


# ---------------- Main Entry Point ----------------
if __name__ == "__main__":
    start_gui()
