Odessy Documentation
====================

Odessy is a local, single-user edge-AI agent runtime. It runs on one computer,
or split across a Jetson (model and voice) and a laptop (controlled command
execution). An optional cloud model backend can replace the local model;
policy, authorization, and confirmation stay local in every configuration.

The model proposes actions. Python Odessy validates and authorizes them. MCP
transports them. The model is never the authority.

.. code-block:: text

    Voice command
        -> local AI reasoning
        -> policy and intent validation
        -> device/tool orchestration
        -> observation and feedback
        -> spoken response

.. toctree::
    :maxdepth: 2
    :caption: User Guide

    user_guide/index

.. toctree::
    :maxdepth: 2
    :caption: Core Concepts

    core_concepts/index

.. toctree::
    :maxdepth: 2
    :caption: Security

    security/credential-handling
    security/filesystem-controls
    security/process-controls
    security/codeql-alert-triage
    security/dependency-and-loopback-fixes

.. toctree::
    :maxdepth: 2
    :caption: UI & Desktop

    ui/index

.. toctree::
    :maxdepth: 2
    :caption: Execution

    execution/index

.. toctree::
    :maxdepth: 2
    :caption: Monitoring

    monitoring/index

.. toctree::
    :maxdepth: 2
    :caption: Qualification

    qualification/index

.. toctree::
    :maxdepth: 2
    :caption: Project

    project/index

.. toctree::
    :hidden:
    :caption: Project Links

    GitHub <https://github.com/odessy/odessy>
