# Limitaciones conocidas

[English](../../LIMITATION.md) | [简体中文](../zh-CN/LIMITATION.md) | **Español**

> Nota: la versión en inglés es la versión canónica; esta traducción es solo de referencia. Las capturas muestran la interfaz en inglés; la aplicación aún no está localizada.

**Developer Preview · v0.5.0-alpha**

La [matriz de soporte actual](current-status.md) es la referencia mantenida de capacidades y de versiones. Esta página resume las restricciones que afectan a la adopción.

- **Instalador:** el [alfa público](https://github.com/Trisoma/odessy/releases/tag/v0.5.0-alpha) es anterior a las correcciones de seguridad de `08efca9`. Usa [el código fuente](getting-started.md) para disponer de ellas. Consulta el [estado de la versión](current-status.md) para ver qué se compiló y qué se comprobó.
- **Seguridad de los puntos de control:** en Windows, la restauración usa un reemplazo anclado a identificadores: cada componente de la ruta se abre en relación con un directorio principal verificado y se rechazan los puntos de reparse; el reemplazo final es un cambio de nombre NT relativo a ese identificador. Si el host no ofrece esos enlaces NT, la restauración devuelve unsupported (no compatible) y la creación, el listado y la vista previa siguen disponibles. Un servicio antiguo en ejecución debe actualizarse para obtener la restauración. Las instantáneas de solo texto no son una copia de seguridad completa y no pueden deshacer comandos ni efectos externos.
- **Diferencias entre motores:** Custom admite una continuación acotada tras un reinicio. El historial de Harness sigue siendo legible, pero no puede reanudarse tras un reinicio a través de este adaptador. Los modos de acceso y la inyección de contexto de Custom no se emulan de forma silenciosa en Harness.
- **Autoridad:** el control total nunca concede nuevos derechos de área de trabajo o remotos ni anula las denegaciones duras. El inicio de sesión SSH comienza con un monitoreo de solo lectura. Custom puede ejecutar comandos aprobados individualmente tras una selección explícita de ejecución SSH. El emparejamiento, el monitoreo y la autorización de ejecución siguen siendo independientes.
- **Shell interactivo:** un shell local aprobado explícitamente tiene autoridad a nivel de usuario del sistema operativo; no se intercepta ni se somete a sandbox comando por comando. La disponibilidad de los ayudantes nativos depende de la plataforma/paquete.
- **Aislamiento:** las comprobaciones de rutas en el espacio de usuario no son un confinamiento completo del núcleo. El aislamiento difiere entre Windows, Linux y Jetson. No exponer la ejecución LAN heredada sin autenticación y en texto plano a redes no confiables.
- **Privacidad:** la configuración en la nube envía los datos de las solicitudes y las observaciones permitidas al proveedor seleccionado. La ocultación de datos sensibles se realiza en la medida de lo posible y no garantiza que no se divulgue información. El historial y la memoria en SQLite son datos locales del perfil, no una bóveda de credenciales cifrada.
- **Hardware y voz:** no se ha establecido una especificación mínima general de hardware ni una compatibilidad universal de audio o de proveedores. Los modelos y los recursos son opcionales y están sujetos a sus propias licencias. macOS y el control general de MCU no están calificados.
- **Coordinación:** los Jobs/Handoffs existentes siguen siendo accesibles, pero el comportamiento compatible depende del motor; la paridad diferida de delegación en Harness no queda implementada solo porque la consulta o una página funcionen.

Consultar los [límites de seguridad detallados](../reference/current-limitations.mdx) (solo en inglés), la [semántica de terminal y puntos de control](../ui/terminal-checkpoints.md) (solo en inglés), el [historial](../ui/conversation-history.md) (solo en inglés) y la [evidencia histórica](./README.md).
