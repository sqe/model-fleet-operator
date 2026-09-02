Model Fleet Operator
====================

Model Fleet Operator is a portable Kubernetes control plane for containerized
model inference, training, validation, and agent workloads. It uses standard
Kubernetes resources and keeps cloud provisioning behind the capacity provider
installed in each cluster.

Choose a path
-------------

* **Platform operators** should start with :doc:`installation`,
  :doc:`operator-guide`, and :doc:`RUNBOOKS`.
* **Researchers and model developers** should start with
  :doc:`researcher-guide`, :doc:`DATASETS`, and :doc:`api-reference`.
* **Security reviewers** should read :doc:`security-permissions`.

View this documentation locally
-------------------------------

From the repository root, install the documentation dependencies, build with
warnings treated as errors, and serve the generated site:

.. code-block:: bash

   make docs-bootstrap
   make docs-build
   python3 -m http.server 8000 --directory docs/_build/html

Open ``http://127.0.0.1:8000``. On macOS, ``open
http://127.0.0.1:8000`` opens it directly. Stop the local server with
``Ctrl-C``.

.. toctree::
   :maxdepth: 2
   :caption: Core documentation

   architecture
   PROJECT_VALUE
   installation
   operator-guide
   researcher-guide
   DATASETS
   WORKLOAD_TEMPLATES
   ARTIFACT_TOOLCHAIN
   PUBLISHING
   api-reference
   security-permissions

.. toctree::
   :maxdepth: 1
   :caption: Feature guides

   GPU_CAPACITY
   CILIUM_GATEWAY
   OBSERVABILITY
   SLACK_COMMANDS
   RUNBOOKS
   TROUBLESHOOTING
   KAFKA_COMMANDS
   MODEL_VALIDATION
   AGENT_CONTROL_PLANE
   QWEN_EXAMPLES

Project status
--------------

The custom-resource API is ``v1alpha1``. The kind path provides a complete local
environment for CPU testing. Cloud and bare-metal installations target an
existing Kubernetes cluster so that network, identity, DNS, and cluster
lifecycle remain under the platform team's control.
