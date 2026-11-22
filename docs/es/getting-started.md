# Primeros pasos

[English](../getting-started.md) | [简体中文](../zh-CN/getting-started.md) | **Español**

**Vista previa para desarrolladores · v0.5.0-alpha**

> Nota: la versión en inglés es la versión canónica; esta traducción es solo de referencia. Las capturas muestran la interfaz en inglés; la aplicación aún no está localizada.

## Elegir tu ruta

Usa esta guía para arrancar Odessy desde una copia del repositorio en Windows. La aplicación inicia el servicio local con el Python de esa copia. Si usas el [instalador publicado para Windows](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha), omite los SDK y sigue el [manual](GOLDEN_PATH.md).

Si solo quieres comprobar Python, empieza con [la prueba con un modelo simulado](CONTRIBUTING.md#comprueba-python-sin-una-clave-de-modelo). Puedes hacerla sin clave ni compilación del escritorio.

## Preparar una copia del código fuente en Windows

Instalar Git, Python 3.13 con el iniciador `py`, Node.js 22, Flutter 3.47.7 stable
y la carga de trabajo **Desktop development with C++** (Desarrollo de escritorio con C++) de Visual Studio con un Windows SDK.
Usar `flutter doctor -v` para comprobar la preparación de la compilación para Windows. No se necesita GPU ni modelo de voz
para el chat escrito. La instalación de dependencias usa acceso a la red.

En PowerShell, desde una carpeta principal de tu elección, usar una copia nueva del código fuente:

```powershell
git clone --branch develop --single-branch https://github.com/Trisoma/odessy.git odessy-preview
Set-Location odessy-preview
$previewRepo = (Get-Location).Path.Replace('\', '/')
$previewPython = "$previewRepo/.venv/Scripts/python.exe"
py -3.13 -m venv .venv
& $previewPython -m pip install -e ".[test,ui]"
Push-Location services/tool-server
npm.cmd ci --no-audit --no-fund
npm.cmd run build
Pop-Location
flutter doctor -v
git rev-parse HEAD
```

Detenerse si un comando falla. Guardar la revisión del código fuente impresa con cualquier informe de error.
No es necesario activar el entorno virtual ni cambiar la política de ejecución de PowerShell.
Rust y Go solo se necesitan al compilar sus componentes opcionales; no son
necesarios para empezar el ejercicio de chat escrito local.

## Iniciar desde la aplicación de escritorio

En esa **misma terminal**:

```powershell
$previewDefines = @(
    '--dart-define=ODESSY_LOCAL_DESKTOP=true'
    "--dart-define=ODESSY_DESKTOP_ROOT=$previewRepo"
    "--dart-define=ODESSY_DESKTOP_PYTHON=$previewPython"
    "--dart-define=ODESSY_SETUP_ROOT=$previewRepo"
    "--dart-define=ODESSY_SETUP_PYTHON=$previewPython"
)
Set-Location apps/desktop
flutter pub get
flutter run -d windows @previewDefines
```

Cuando se abra Odessy, sigue el manual para [conectar un modelo y obtener una respuesta](GOLDEN_PATH.md#2-configurar-deepseek). Después, [ejecuta un comando local y revisa Output](GOLDEN_PATH.md#3-ejecutar-un-comando-en-tu-portátil-windows).

La voz y SSH son pasos siguientes opcionales. La configuración desde el código fuente no descarga sus recursos ni
compila los ayudantes opcionales de monitoreo y contraseñas. Consultar [componentes de voz](../setup/voice-components.md) (solo en inglés)
y [configuración de SSH](../setup/ssh-computers.md) (solo en inglés) para sus requisitos separados.

## Diagnosticar un inicio fallido

Si el escritorio se abre desconectado, comprueba que usaste las cinco opciones en la misma sesión de PowerShell. `flutter run -d windows` por sí solo abre un cliente sin servicio administrado. El arranque desde el código usa `build/local-desktop/state` y puede guardar una referencia a la configuración en los ajustes del usuario. Consulta los [modos de arranque](../ui/README.md#connection-and-launch-modes).

Empieza por el error en Settings. Para diagnosticar el arranque desde código, abre PowerShell en la raíz de la copia. El ejemplo usa DeepSeek; cámbialo por el proveedor que configuraste.

```powershell
$previewRepo = (Get-Location).Path.Replace('\', '/')
$previewPython = "$previewRepo/.venv/Scripts/python.exe"
$env:ODESSY_CONFIG_FILE = "$previewRepo/build/local-desktop/state/odessy.env"
& $previewPython -m odessy doctor --model-backend deepseek
```

Doctor puede consultar los endpoints configurados y listar herramientas. No ejecuta inferencia en la nube ni prueba el audio. La clave de sesión se queda en el escritorio; no la copies a un archivo para esta prueba.

Usar los [pasos de solución de problemas y de reporte saneado](./GOLDEN_PATH.md#solución-de-problemas-y-reporte) del manual.
Para pruebas deterministas de componentes o un servidor de API gestionado manualmente, consultar las
[comprobaciones avanzadas de desarrollo](./CONTRIBUTING.md#comprobaciones-avanzadas-de-componentes-y-api).
La ruta de CI y de código fuente para Linux sigue disponible allí; esta guía de Windows no
califica paquetes de escritorio para Linux/macOS.
