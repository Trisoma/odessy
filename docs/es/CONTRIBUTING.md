# Contribuir a Odessy

[English](../../CONTRIBUTING.md) | [简体中文](../zh-CN/CONTRIBUTING.md) | **Español**

**Vista previa para desarrolladores · v0.5.0-alpha**

> Nota: la versión en inglés es la versión canónica; esta traducción es solo de referencia. Las capturas muestran la interfaz en inglés; la aplicación aún no está localizada.

¡Bienvenido a bordo! ⛵ Si necesitas arrancar el escritorio, sigue [Primeros pasos](getting-started.md). Para tu primer PR, elige un issue abierto en la [hoja de ruta](../ROADMAP.md), reproduce el problema y ejecuta la prueba correspondiente. Solo necesitas las herramientas del componente que vas a cambiar.

[Comprueba Python sin una clave de modelo](#comprueba-python-sin-una-clave-de-modelo) antes de compilar la aplicación de escritorio.

## Elegir por dónde empezar

| Cambio | Punto de partida | Carpeta | Primera prueba |
| --- | --- | --- | --- |
| Salida registrada [#72](https://github.com/Trisoma/odessy/issues/72) | [`workspace_inspector.dart`](../../apps/desktop/lib/features/workspaces/presentation/workspace_inspector.dart) | `apps/desktop` | `flutter test test/workspace_inspector_test.dart test/saved_chat_navigation_test.dart` |
| Estado de la conexión SSH [#70](https://github.com/Trisoma/odessy/issues/70) | [Vistas de dispositivos](../../apps/desktop/lib/features/devices/presentation/) | `apps/desktop` | `flutter test test/ssh_connection_test.dart test/device_page_test.dart` |
| Detección de Piper [#69](https://github.com/Trisoma/odessy/issues/69) | [`setup/voice_assets.py`](../../src/odessy/setup/voice_assets.py) | Raíz del repositorio | `python -m pytest tests/test_piper_provider.py tests/test_doctor.py -q` |
| API de la aplicación Python | [`application/api.py`](../../src/odessy/application/api.py) | Raíz del repositorio | `python -m pytest tests/test_application_api.py -q` |
| Herramientas MCP | [`tools/src/`](../../tools/src/) | `tools` | `npm run typecheck` · `npm test` |
| Cliente Dart | [`packages/odessy_client/`](../../packages/odessy_client/) | `packages/odessy_client` | `dart analyze` · `dart test` |
| Monitoreo | [`services/monitoring/`](../../services/monitoring/) | `services/monitoring` | `go test ./... -count=1` |
| Emparejamiento | [`native/odessy-link/`](../../native/odessy-link/) | `native/odessy-link` | `cargo test --locked` |

Para mediciones de TTS, consulta la [guía de benchmarks](../../benchmark/tts/README.md) y el [#71](https://github.com/Trisoma/odessy/issues/71).

Antes de ejecutar los comandos, prepara el [entorno de desarrollo](#configuración-del-entorno-de-desarrollo). Para cambios de documentación, comprueba los enlaces e imágenes locales, verifica los comandos que edites y previsualiza el Markdown de cada idioma afectado.

Aceptamos PR pequeños de documentación, pruebas, CI y refactorizaciones que preserven el comportamiento. Explica qué arreglas, qué comportamiento o controles se conservan y cómo lo verificaste. Para un cambio mayor, acuerda el alcance en un issue antes de escribir código. Comunica las vulnerabilidades en privado. [Política de seguridad](../../SECURITY.md)

## Ruta hacia la responsabilidad de un área

Comenta en el issue cómo lo reprodujiste y qué quieres cambiar. Empieza por un cambio y su prueba. Tras varias contribuciones revisadas, puedes proponer encargarte de los documentos, las pruebas y la clasificación de errores de un área. Acuerda el alcance y tu disponibilidad con el mantenedor. El acceso al repositorio y los roles de mantenimiento requieren una decisión aparte.

Pide revisión del mantenedor para cambios en política y despacho, credenciales, identidad y emparejamiento de equipos, propiedad de la instalación o restauración segura. El escritorio y el cliente Dart tienen reglas de revisión propias para controladores y contratos compartidos. [Arquitectura](ARCHITECTURE.md) · [Instrucciones del escritorio](../../apps/desktop/AGENTS.md) · [Instrucciones del cliente Dart](../../packages/odessy_client/AGENTS.md)

## Antes de abrir un issue

Buscar issues y pull requests existentes, incluidos los cerrados y fusionados, para evitar trabajo duplicado. Revisar el código fuente pertinente y las [limitaciones conocidas](./LIMITATION.md) para conocer el comportamiento y las restricciones existentes. Si ya hay un PR que aborda el problema, considerar ayudar a revisarlo o mejorarlo.

Mantener cada issue centrado en un solo problema. Para un informe de error, incluir:

- La versión o el commit, el sistema operativo, la arquitectura y el backend del modelo.
- Los pasos para reproducir el problema, incluidos los comandos ejecutados.
- El comportamiento esperado y el real, con diagnósticos sin datos sensibles cuando sea útil.

Para una propuesta de funcionalidad, describir el caso de uso, el comportamiento deseado y las alternativas consideradas. Para trabajo más amplio, comentar en el issue antes de empezar para que otras personas contribuyentes puedan evitar esfuerzos superpuestos.

## Preparar un pull request

Apuntar a `develop` y mantener el cambio lo bastante pequeño como para revisarlo. Separar las correcciones no relacionadas, los cambios de formato y las actualizaciones de dependencias.

Antes de solicitar la revisión:

- Explicar el problema y cómo lo resuelve el cambio. Vincular los issues relacionados.
- Añadir o actualizar pruebas para los cambios de comportamiento. Nunca eliminar pruebas ni debilitar las aserciones de seguridad solo para que pasen las comprobaciones. Usar [LIMITATION.md](./LIMITATION.md) para ayudar a determinar si debe corregirse la implementación o una expectativa de prueba.
- Actualizar la documentación cuando cambien la configuración del entorno, la configuración de la aplicación o el comportamiento visible para el usuario.
- Enumerar las comprobaciones exactas ejecutadas y sus resultados, incluidas las fallas, las comprobaciones omitidas y cualquier validación que no se haya podido realizar.
- Eliminar secretos y datos privados del código, los fixtures, los registros y las capturas de pantalla.

## Registro de cambios

Para los cambios visibles para el usuario, añadir una entrada breve en **Unreleased** de [CHANGELOG.md](../../CHANGELOG.md) (solo en inglés). Describir el efecto en los usuarios; omitir la limpieza interna y las ediciones que solo corrigen erratas. Los mantenedores agrupan las entradas bajo una versión y una fecha de publicación al preparar una versión. Mantener la evidencia detallada de calificación y las limitaciones conocidas en las notas de la versión dentro de `docs/releases/`.

## Contribuciones asistidas por IA

Las contribuciones asistidas por IA son bienvenidas; nuestro equipo también usa estas herramientas. Indicar en el PR cuándo se usó asistencia de IA y describir qué se revisó y verificó personalmente. Cada persona sigue siendo responsable de comprender el código enviado, comprobar su comportamiento y confirmar que tiene derecho a contribuirlo.

## Secretos y seguridad

Nunca confirmar claves de API, tokens, grabaciones privadas ni credenciales específicas de una máquina. Ocultar los valores sensibles y la información personal antes de compartir diagnósticos. Si se descubre una vulnerabilidad, usar las instrucciones de reporte privado de [SECURITY.md](../../SECURITY.md) (solo en inglés).

## Configuración del entorno de desarrollo

Para arrancar el escritorio completo en Windows, sigue [Primeros pasos](getting-started.md). Si solo necesitas una prueba de componente, prepara lo que indique la tabla.

### Qué espera el inicio rápido

| Componente | Preparación |
| --- | --- |
| Python | Usa Python 3.10–3.13 en un entorno virtual. Desde la raíz, ejecuta `python -m pip install -e ".[test,ui]"`. |
| Servidor MCP | Usa Node 22 (también para las pruebas de integración de Python) o 24. Desde `tools/`, ejecuta `npm ci --no-audit --no-fund` y `npm run build`. |
| Flutter y Dart | Usa Flutter 3.47.7 estable con su SDK de Dart. Ejecuta `flutter pub get` en `apps/desktop` o `dart pub get` en `packages/odessy_client`. |
| Monitoreo | Usa la versión de Go indicada en [go.mod](../../services/monitoring/go.mod). |
| Emparejamiento | Usa Rust estable y Cargo. |

Desde la raíz del repositorio, crea y activa el entorno de Python en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

En Linux, crea el entorno con el mismo primer comando y ejecuta `source .venv/bin/activate`. Para las pruebas con modelo simulado o al estilo del CI, apunta `ODESSY_CONFIG_FILE` a un archivo vacío que crees en una copia desechable para evitar que se cargue tu configuración real.

```powershell
$env:ODESSY_CONFIG_FILE = "$PWD/.ci-no-config.env"
$env:PYTHONUTF8 = "1"
```

En Linux, usa `export ODESSY_CONFIG_FILE="$PWD/.ci-no-config.env"` y `export PYTHONUTF8=1`.

Instala `pytest-xdist>=3.6,<4` si vas a ejecutar la prueba paralela de Python de abajo.

## Comprueba Python sin una clave de modelo

Después de [preparar el entorno de Python](#configuración-del-entorno-de-desarrollo), ejecuta este comando desde la raíz del repositorio con el entorno activo y la configuración vacía seleccionada. La respuesta debería empezar por `The current time is`. Esta prueba comprueba la CLI de Python con un modelo simulado.

```text
python -m odessy --fake-model "what time is it?"
```

## Ejecutar la aplicación de escritorio

Sigue [Primeros pasos](getting-started.md) para el arranque administrado y el ejercicio con un modelo real.

## Comprobaciones avanzadas de componentes y API

La CLI del módulo sigue implementada para diagnósticos, uso sin interfaz y pruebas de integración. Estos comandos de servidor manual no son necesarios para usar el escritorio gestionado. Todo lo siguiente no necesita un proveedor de pago, un micrófono ni un host remoto. Un modelo falso determinista comprueba la conexión interna de los componentes; no valida un modelo real, la experiencia de aprobación en vivo, el habla ni la ejecución remota.

Iniciar el backend (esto imprime la dirección de loopback; la API necesita un token):

```powershell
$env:ODESSY_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
python -m odessy serve --fake-model
```

Iniciar la aplicación de escritorio en una segunda terminal:

```powershell
cd apps/desktop
flutter run -d windows
```

En Linux, generar un token local e iniciar el servidor con:

```sh
export ODESSY_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python -m odessy serve --fake-model
```

Después, ejecutar `flutter run -d linux` desde `apps/desktop`. Usar el token generado al conectar la aplicación de escritorio. Mostrarlo localmente con `$env:ODESSY_API_TOKEN` en PowerShell o `printf '%s\n' "$ODESSY_API_TOKEN"` en Linux antes de introducirlo en la aplicación. Mantenerlo privado. Generar un token distinto en cada máquina.

Hacer clic en el icono de conexión, introducir la dirección de loopback impresa y el token, y luego enviar `what time is it?`. El token permanece en la memoria de la aplicación; el backend nunca lo imprime. Sin `--fake-model`, los mismos pasos usan el modelo real configurado y su presupuesto existente, y eso es lo que hace que las comprobaciones con proveedores en vivo sean trabajo aparte. Borrar primero la anulación del inicio rápido (`unset ODESSY_CONFIG_FILE` en Linux o `Remove-Item Env:ODESSY_CONFIG_FILE` en PowerShell), o apuntarla a la configuración personal. De lo contrario, la ejecución en vivo seguirá usando `.ci-no-config.env`.

## Diagnosticar este equipo

```sh
python -m odessy doctor
python -m odessy doctor --voice
```

`doctor` nombra cada paso fallido u opcional junto con una solución, por ejemplo un endpoint de modelo que no responde, un directorio `tools/` sin compilar o los recursos opcionales de habla y de palabra de activación. Las comprobaciones de Piper y de proveedores personalizados están incompletas; usar también sus pruebas controladas de proveedores. Un `[FAIL]` para el endpoint del modelo es esperable antes de que se ejecute Ollama (u otro endpoint) y no bloquea las comprobaciones de modelo falso anteriores. No descarga recursos ni realiza inferencia en la nube, pero puede sondear endpoints configurados y listar herramientas de terceros. No es una réplica completa de las credenciales de sesión del escritorio ni una prueba de aceptación de audio físico. Mantener el error visible del escritorio y la configuración seleccionada junto a su salida.

## Ejecutar las comprobaciones pertinentes

Ejecutar las comprobaciones de los componentes modificados e incluir los comandos y resultados exactos en el PR. Indicar qué comprobaciones no se pudieron ejecutar y por qué.

### Entorno de ejecución de Python

Desde la raíz del repositorio:

```sh
python -m pytest -q -n 2 --dist loadfile --tb=short --durations=10
python -m odessy --help
python -m odessy --fake-model "what time is it?"
python -m odessy --fake-model "list ."
```

La CI comprueba que las respuestas del modelo falso comiencen con `The current time is ` y `Directory entries: ` respectivamente, y que la respuesta del directorio incluya `pyproject.toml`. Son comprobaciones de conexión interna, no una aceptación de modelo real.

### Servidor de herramientas Node

Desde `tools/`:

```sh
npm ci --no-audit --no-fund
npm run typecheck
npm test
```

### Transporte y emparejamiento en Rust

Desde `native/odessy-link/`:

```sh
cargo test --locked
cargo clippy --all-targets -- -D warnings
cargo build --locked
```

Para la integración emparejada, compilar primero Node y Rust, establecer `ODESSY_LINK_E2E=1` (PowerShell: `$env:ODESSY_LINK_E2E="1"`) y luego ejecutar en la raíz:

```sh
python -m pytest -q tests/test_paired_transport.py tests/test_paired_integration.py tests/test_paired_agent.py tests/test_paired_host.py
```

### Monitoreo de equipos remotos (SSH)

Usar la [ruta de contribución para SSH](../../src/odessy/ssh/README.md) (solo en inglés) para: un alias existente → probar la conexión → el primer estado con marca temporal → desconectar y reconectar. Incluye los comandos de fixture para un solo portátil, la unión entre proveedor y ciclo de vida, y la solución de problemas. El monitoreo sigue siendo de solo lectura. La selección separada de ejecución SSH de Custom expone `ssh.run` solo después de una acción explícita **Use for agent commands** (Usar para comandos del agente); cada comando remoto exacto requiere aprobación en todos los modos. Se ejecuta como la cuenta SSH sin un sandbox remoto. La ejecución SSH de Harness, la edición remota de archivos y la PTY remota no son compatibles. Consultar [el límite de comandos SSH](../setup/ssh-computers.md) (solo en inglés). Los hosts emparejados ya autenticados siguen siendo un transporte compatible aparte.

### Confinamiento real en Linux

Estas comprobaciones requieren bubblewrap, util-linux y espacios de nombres de usuario sin privilegios utilizables. Consultar la CI para la configuración de AppArmor específica del runner. Establecer `ODESSY_SANDBOX_LIVE=1` y luego ejecutar en la raíz:

```sh
python -m pytest -q tests/test_sandbox_live.py
```

Desde `tools/`, ejecutar `node --test dist/tests/sandbox-live.test.js`. La comprobación de `doctor` en la CI es `python -m odessy doctor | tee doctor.txt; grep -q "read-only: bwrap (full; network cut, credentials hidden)" doctor.txt` bajo Bash en la raíz. Estas comprobaciones necesitan un kernel Linux real; Windows no es suficiente.

### Aplicación de escritorio y cliente Dart

Desde la raíz del repositorio:

```sh
python tool/check_desktop_contracts.py
python -m unittest discover -s tests -p test_desktop_settings_inventory.py -v
```

Desde `packages/odessy_client/`, con el SDK de Dart de Flutter 3.47.7:

```sh
dart pub get
dart format --output=none --set-exit-if-changed lib test
dart analyze
dart test
```

Desde `apps/desktop/`:

```sh
flutter pub get
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
```

Las comprobaciones de hardware, audio físico, emparejamiento en red real y proveedores en vivo son trabajo de aceptación aparte. Indicar explícitamente cuándo no se ejecutaron.

## Licencia de las contribuciones

Al enviar una contribución, se confirma que se tiene derecho a enviarla y se acepta licenciarla bajo la [licencia Apache-2.0](../../LICENSE) (solo en inglés) del proyecto.
