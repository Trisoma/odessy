#include "my_application.h"

#include <flutter_linux/flutter_linux.h>

#include <algorithm>

#ifdef GDK_WINDOWING_X11
#include <gdk/gdkx.h>
#endif

#include "flutter/generated_plugin_registrant.h"

struct _MyApplication {
  GtkApplication parent_instance;
  char** dart_entrypoint_arguments;
};

G_DEFINE_TYPE(MyApplication, my_application, GTK_TYPE_APPLICATION)

namespace {

// Logical (device independent) sizes. The Windows runner opens the same
// footprint, so the installer reads as one product on both platforms.
constexpr int kSetupWidth = 900;
constexpr int kSetupHeight = 680;
constexpr int kSetupMinWidth = 720;
constexpr int kSetupMinHeight = 540;
constexpr int kMainWidth = 1200;
constexpr int kMainHeight = 850;
// Keep a margin so a window that nearly fills the work area still shows edges.
constexpr int kWorkAreaMargin = 24;

enum class LaunchMode { kApplication, kSetup, kManage, kUninstall, kInstalled };

// The mode is decided from the arguments before Flutter starts; it is never
// inferred from the window title.
LaunchMode LaunchModeFor(char** arguments) {
  bool installed = false;
  bool uninstall = false;
  bool setup = false;
  bool manage = false;
  for (int index = 0; arguments != nullptr && arguments[index] != nullptr;
       ++index) {
    const char* argument = arguments[index];
    if (g_strcmp0(argument, "--installed") == 0) {
      installed = true;
    } else if (g_strcmp0(argument, "--uninstall") == 0) {
      uninstall = true;
    } else if (g_strcmp0(argument, "--setup") == 0) {
      setup = true;
    } else if (g_strcmp0(argument, "--manage") == 0) {
      manage = true;
    }
  }
  if (installed) return LaunchMode::kInstalled;
  if (uninstall) return LaunchMode::kUninstall;
  if (setup) return LaunchMode::kSetup;
  if (manage) return LaunchMode::kManage;
  return LaunchMode::kApplication;
}

bool IsSetupSurface(LaunchMode mode) {
  return mode == LaunchMode::kSetup || mode == LaunchMode::kManage ||
         mode == LaunchMode::kUninstall;
}

const char* TitleFor(LaunchMode mode) {
  switch (mode) {
    case LaunchMode::kSetup:
      return "Odessy Setup";
    case LaunchMode::kManage:
      return "Manage Odessy";
    case LaunchMode::kUninstall:
      return "Uninstall Odessy";
    default:
      return "Odessy";
  }
}

// Shrink a requested size so it still fits the monitor work area. Monitors
// report application pixels, which is also what GtkWindow expects here.
void FitToWorkArea(GtkWindow* window, int* width, int* height) {
  GdkDisplay* display = gtk_widget_get_display(GTK_WIDGET(window));
  if (display == nullptr) return;
  GdkMonitor* monitor = gdk_display_get_primary_monitor(display);
  if (monitor == nullptr) monitor = gdk_display_get_monitor(display, 0);
  if (monitor == nullptr) return;
  GdkRectangle area = {};
  gdk_monitor_get_workarea(monitor, &area);
  if (area.width > 0) {
    *width = std::min(*width, std::max(area.width - kWorkAreaMargin, 320));
  }
  if (area.height > 0) {
    *height = std::min(*height, std::max(area.height - kWorkAreaMargin, 240));
  }
}

bool EnvironmentFlagEnabled(const char* name) {
  const char* value = g_getenv(name);
  if (value == nullptr || *value == '\0') return false;
  return g_ascii_strcasecmp(value, "0") != 0 &&
         g_ascii_strcasecmp(value, "false") != 0 &&
         g_ascii_strcasecmp(value, "no") != 0 &&
         g_ascii_strcasecmp(value, "off") != 0;
}

}  // namespace

// Called when first Flutter frame received.
static void first_frame_cb(MyApplication* self, FlView* view) {
  gtk_widget_show(gtk_widget_get_toplevel(GTK_WIDGET(view)));
}

// Implements GApplication::activate.
static void my_application_activate(GApplication* application) {
  MyApplication* self = MY_APPLICATION(application);
  GtkWindow* window =
      GTK_WINDOW(gtk_application_window_new(GTK_APPLICATION(application)));

  // Use a header bar when running in GNOME as this is the common style used
  // by applications and is the setup most users will be using (e.g. Ubuntu
  // desktop).
  // If running on X and not using GNOME then just use a traditional title bar
  // in case the window manager does more exotic layout, e.g. tiling.
  // If running on Wayland assume the header bar will work (may need changing
  // if future cases occur).
  gboolean use_header_bar = TRUE;
#ifdef GDK_WINDOWING_X11
  GdkScreen* screen = gtk_window_get_screen(window);
  if (GDK_IS_X11_SCREEN(screen)) {
    const gchar* wm_name = gdk_x11_screen_get_window_manager_name(screen);
    if (g_strcmp0(wm_name, "GNOME Shell") != 0) {
      use_header_bar = FALSE;
    }
  }
#endif
  const LaunchMode launch_mode = LaunchModeFor(self->dart_entrypoint_arguments);
  const char* title = TitleFor(launch_mode);

  if (use_header_bar) {
    GtkHeaderBar* header_bar = GTK_HEADER_BAR(gtk_header_bar_new());
    gtk_widget_show(GTK_WIDGET(header_bar));
    gtk_header_bar_set_title(header_bar, title);
    gtk_header_bar_set_show_close_button(header_bar, TRUE);
    gtk_window_set_titlebar(window, GTK_WIDGET(header_bar));
  } else {
    gtk_window_set_title(window, title);
  }

  const bool setup_surface = IsSetupSurface(launch_mode);
  int width = setup_surface ? kSetupWidth : kMainWidth;
  int height = setup_surface ? kSetupHeight : kMainHeight;
  FitToWorkArea(window, &width, &height);
  gtk_window_set_default_size(window, width, height);

  g_autoptr(FlDartProject) project = fl_dart_project_new();
  fl_dart_project_set_dart_entrypoint_arguments(
      project, self->dart_entrypoint_arguments);
  // Impeller renders through OpenGL ES and produces incomplete frames on
  // graphics stacks without working acceleration (virtual machines, remote
  // desktops, older drivers). Set ODESSY_DISABLE_IMPELLER to fall back to Skia
  // without rebuilding Flutter. The opt-out is explicit and off by default.
  if (EnvironmentFlagEnabled("ODESSY_DISABLE_IMPELLER")) {
    fl_dart_project_set_enable_impeller(project, FALSE);
  }

  FlView* view = fl_view_new(project);
  if (setup_surface) {
    // A size request on the view expresses the floor in content (application)
    // pixels, the same unit the Windows runner uses. Geometry hints would be
    // applied to the outer window and let the CSD title bar eat into the
    // usable area. Never exceed the size actually granted by the work area,
    // otherwise the window could not be created on a small display.
    gtk_widget_set_size_request(GTK_WIDGET(view),
                                std::min(kSetupMinWidth, width),
                                std::min(kSetupMinHeight, height));
  }
  GdkRGBA background_color;
  // Background defaults to black, override it here if necessary, e.g. #00000000
  // for transparent.
  gdk_rgba_parse(&background_color, "#000000");
  fl_view_set_background_color(view, &background_color);
  gtk_widget_show(GTK_WIDGET(view));
  gtk_container_add(GTK_CONTAINER(window), GTK_WIDGET(view));

  // Show the window when Flutter renders.
  // Requires the view to be realized so we can start rendering.
  g_signal_connect_swapped(view, "first-frame", G_CALLBACK(first_frame_cb),
                           self);
  gtk_widget_realize(GTK_WIDGET(view));

  fl_register_plugins(FL_PLUGIN_REGISTRY(view));

  gtk_widget_grab_focus(GTK_WIDGET(view));
}

// Implements GApplication::local_command_line.
static gboolean my_application_local_command_line(GApplication* application,
                                                  gchar*** arguments,
                                                  int* exit_status) {
  MyApplication* self = MY_APPLICATION(application);
  // Strip out the first argument as it is the binary name.
  self->dart_entrypoint_arguments = g_strdupv(*arguments + 1);

  g_autoptr(GError) error = nullptr;
  if (!g_application_register(application, nullptr, &error)) {
    g_warning("Failed to register: %s", error->message);
    *exit_status = 1;
    return TRUE;
  }

  g_application_activate(application);
  *exit_status = 0;

  return TRUE;
}

// Implements GApplication::startup.
static void my_application_startup(GApplication* application) {
  // MyApplication* self = MY_APPLICATION(object);

  // Perform any actions required at application startup.

  G_APPLICATION_CLASS(my_application_parent_class)->startup(application);
}

// Implements GApplication::shutdown.
static void my_application_shutdown(GApplication* application) {
  // MyApplication* self = MY_APPLICATION(object);

  // Perform any actions required at application shutdown.

  G_APPLICATION_CLASS(my_application_parent_class)->shutdown(application);
}

// Implements GObject::dispose.
static void my_application_dispose(GObject* object) {
  MyApplication* self = MY_APPLICATION(object);
  g_clear_pointer(&self->dart_entrypoint_arguments, g_strfreev);
  G_OBJECT_CLASS(my_application_parent_class)->dispose(object);
}

static void my_application_class_init(MyApplicationClass* klass) {
  G_APPLICATION_CLASS(klass)->activate = my_application_activate;
  G_APPLICATION_CLASS(klass)->local_command_line =
      my_application_local_command_line;
  G_APPLICATION_CLASS(klass)->startup = my_application_startup;
  G_APPLICATION_CLASS(klass)->shutdown = my_application_shutdown;
  G_OBJECT_CLASS(klass)->dispose = my_application_dispose;
}

static void my_application_init(MyApplication* self) {}

MyApplication* my_application_new() {
  // Set the program name to the application ID, which helps various systems
  // like GTK and desktop environments map this running application to its
  // corresponding .desktop file. This ensures better integration by allowing
  // the application to be recognized beyond its binary name.
  g_set_prgname(APPLICATION_ID);

  return MY_APPLICATION(g_object_new(my_application_get_type(),
                                     "application-id", APPLICATION_ID, "flags",
                                     G_APPLICATION_NON_UNIQUE, nullptr));
}
