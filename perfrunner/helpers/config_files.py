import json
from enum import Enum
from pathlib import Path
from typing import Callable

import yaml
from decorator import decorator

from logger import logger
from perfrunner.settings import BucketSettings, ClusterSpec, Config, TestConfig


@decorator
def supported_for(
    method: Callable, since: tuple = None, upto: tuple = None, feature: str = None, *args, **kwargs
):
    version = args[0].version or (0, 0, 0)
    if not since or since <= version:
        if not upto or upto >= version:
            return method(*args, **kwargs)

    msg_args = [
        f"Ignoring setting {feature}. Feature only supported",
        f"from version {since}" if since else "",
        f"upto version {upto}" if upto else "",
    ]
    logger.warn(" ".join(msg_args))


class FileType(Enum):
    INI = 0
    YAML = 1
    JSON = 2


class ConfigFile:
    """Base helper class for interacting with YAML, JSON and INI files.

    Classes inheriting from this can be used with or without a context manager.
    With context manager, `load_config()` will be called automatic during entering the context and
    `write()` will be called when exiting the context. When not using a context manager, one will
    need to manually call `load_config()` and appropriately call `write()`. This is useful when
    loading configuration is not needed, and rather just source or dest paths are needed.
    For example: `MyObjectClass().dest_file`.
    """

    def __init__(self, file_path: str, file_type: FileType = None):
        self.source_file = self.dest_file = file_path
        self.file_type = file_type or self._get_type()

    def _get_type(self):
        if self.source_file.endswith(".yaml"):
            return FileType.YAML
        elif self.source_file.endswith(".json"):
            return FileType.JSON
        return FileType.INI

    def __enter__(self):
        self.load_config()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.write()

    def load_config(self):
        # Some YAML files may contain more that one dict objects. This is stored in `all_config`.
        self.all_configs = self.read_file()
        # For most other use cases, `config` is the content of the whole file.
        self.config = self.all_configs[0]

    def reload_from_dest(self):
        """Reload the content of a destination file and use it for config data.

        This is useful when source and dest files are different and the dest file has been written
        to before. It should be called first before updating anything in the config.
        """
        self.source_file = self.dest_file
        self.load_config()

    def read_file(self) -> list[dict]:
        """Read the content of the source file based on the file type.

        Returns a list of python dict objects as read from the file. For most file types, this will
        be just one object. Some YAML files may, however, contain more than one objetcs.
        """
        # If the source file does not exist, return an empty dict.
        # File will be created when calling write()
        if not Path(self.source_file).is_file():
            return [{}]

        if self.file_type != FileType.INI:
            with open(self.source_file) as file:
                return (
                    list(yaml.load_all(file, Loader=yaml.FullLoader))
                    if self.file_type == FileType.YAML
                    else [json.load(file)]
                )
        else:
            self.ini_config = Config()
            self.ini_config.parse(self.source_file)
            return [self.ini_config._get_options_as_dict() or {}]

    def write(self):
        """Write the content of all configurations to the destination file."""
        if self.file_type != FileType.INI:
            with open(self.dest_file, "w") as file:
                if self.file_type == FileType.YAML:
                    yaml.dump_all(self.all_configs, file)
                else:
                    json.dump(self.config, file, indent=4)
        else:
            self.ini_config.update_spec_file(self.dest_file)


class CAOFiles(ConfigFile):
    """Base class for all CAO YAML configurations."""

    def __init__(self, template_name: str, version: str):
        path = f"cloud/operator/templates/{template_name}.yaml"
        super().__init__(path, FileType.YAML)
        self.version = version
        dest_name = template_name.removesuffix("_template")
        self.dest_file = f"cloud/operator/{dest_name}.yaml"


class CAOConfigFile(CAOFiles):
    """CAO config file for deploying the operator and admission controller.

    It configures:
    - CAO and admission controller deployment (pods and services)
    - CAO and admission controller service account
    - Role and role bindings
    """

    def __init__(self, version: str, operator_tag: str, controller_tag: str):
        super().__init__("config_template", version)
        self.operator_tag = operator_tag
        self.controller_tag = controller_tag

    def setup_config(self):
        self._inject_config_tag()
        self._inject_version_annotations()

    def _inject_config_tag(self):
        for config in self.all_configs:
            if config["kind"] != "Deployment":
                continue
            if config["metadata"]["name"] == "couchbase-operator":
                config["spec"]["template"]["spec"]["containers"][0]["image"] = self.operator_tag
            else:
                config["spec"]["template"]["spec"]["containers"][0]["image"] = self.controller_tag

    def _inject_version_annotations(self):
        for config in self.all_configs:
            config["metadata"]["annotations"]["config.couchbase.com/version"] = self.version


class CAOCouchbaseClusterFile(CAOFiles):
    """Couchbase cluster configuration."""

    def __init__(self, version: str, cluster_spec: ClusterSpec, test_config: TestConfig = None):
        super().__init__("couchbase-cluster_template", version)
        self.test_config = test_config or TestConfig()
        self.cluster_spec = cluster_spec

    def get_cluster_name(self) -> str:
        return self.config["metadata"]["name"]

    def set_server_spec(self, server_tag: str, server_count: int):
        self.config["spec"]["image"] = server_tag
        self.config["spec"]["servers"][0]["size"] = server_count

    def set_backup(self, backup_tag: str):
        # Only setup backup for backup tests
        if backup_tag:
            self.config["spec"]["backup"]["image"] = backup_tag
        else:
            self.config.get("spec", {}).pop("backup")

    def set_exporter(self, exporter_tag: str, refresh_rate: int):
        # Only deploy the exporter sidecar if requested
        if exporter_tag:
            self.config["spec"]["monitoring"]["prometheus"].update(
                {"image": exporter_tag, "refreshRate": refresh_rate}
            )
        else:
            self.config.get("spec", {}).pop("monitoring")

    def set_memory_quota(self):
        self.config["spec"]["cluster"].update(
            {
                "dataServiceMemoryQuota": f"{self.test_config.cluster.mem_quota}Mi",
                "indexServiceMemoryQuota": f"{self.test_config.cluster.index_mem_quota}Mi",
            }
        )

        if self.test_config.cluster.fts_index_mem_quota:
            self.config["spec"]["cluster"].update(
                {"searchServiceMemoryQuota": f"{self.test_config.cluster.fts_index_mem_quota}Mi"}
            )
        if self.test_config.cluster.analytics_mem_quota:
            self.config["spec"]["cluster"].update(
                {"analyticsServiceMemoryQuota": f"{self.test_config.cluster.analytics_mem_quota}Mi"}
            )
        if self.test_config.cluster.eventing_mem_quota:
            self.config["spec"]["cluster"].update(
                {"eventingServiceMemoryQuota": f"{self.test_config.cluster.eventing_mem_quota}Mi"}
            )

    def set_index_settings(self):
        index_nodes = self.cluster_spec.servers_by_role("index")
        settings = self.test_config.gsi_settings.settings
        if index_nodes and settings:
            self.config["spec"]["cluster"].update(
                {"indexStorageSetting": settings["indexer.settings.storage_mode"]}
            )

    def set_services(self):
        server_types = dict()
        server_roles = self.cluster_spec.roles
        for _, role in server_roles.items():
            role = role.replace("kv", "data").replace("n1ql", "query")
            server_type_count = server_types.get(role, 0)
            server_types[role] = server_type_count + 1

        istio = str(self.cluster_spec.istio_enabled(cluster_name="k8s_cluster_1")).lower()

        cluster_servers = []
        volume_claims = []
        for server_role, server_role_count in server_types.items():
            node_selector = {
                f"{service.replace('data', 'kv').replace('query', 'n1ql')}_enabled": "true"
                for service in server_role.split(",")
            }
            node_selector["NodeRoles"] = "couchbase1"
            spec = {
                "imagePullSecrets": [{"name": "regcred"}],
                "nodeSelector": node_selector,
            }

            sg_name = server_role.replace(",", "-")
            sg_def = self.test_config.get_sever_group_definition(sg_name)
            sg_nodes = int(sg_def.get("nodes", server_role_count))
            volume_size = sg_def.get("volume_size", "1000GB")
            volume_size = volume_size.replace("GB", "Gi")
            volume_size = volume_size.replace("MB", "Mi")
            pod_def = {
                "spec": spec,
                "metadata": {"annotations": {"sidecar.istio.io/inject": istio}},
            }

            server_def = {
                "name": sg_name,
                "services": server_role.split(","),
                "pod": pod_def,
                "size": sg_nodes,
                "volumeMounts": {"default": sg_name},
            }

            volume_claim_def = {
                "metadata": {"name": sg_name},
                "spec": {"resources": {"requests": {"storage": volume_size}}},
            }
            cluster_servers.append(server_def)
            volume_claims.append(volume_claim_def)

        self.config["spec"]["servers"] = cluster_servers
        self.config["spec"]["volumeClaimTemplates"] = volume_claims

    def configure_auto_compaction(self):
        compaction_settings = self.test_config.compaction
        db_percent = int(compaction_settings.db_percentage)
        views_percent = int(compaction_settings.view_percentage)

        self.config["spec"]["cluster"].update(
            {
                "autoCompaction": {
                    "databaseFragmentationThreshold": {"percent": db_percent},
                    "viewFragmentationThreshold": {"percent": views_percent},
                    "parallelCompaction": bool(str(compaction_settings.parallel).lower()),
                }
            }
        )

    def set_auto_failover(self):
        enabled = self.test_config.bucket.autofailover_enabled
        failover_timeouts = self.test_config.bucket.failover_timeouts
        disk_failover_timeout = self.test_config.bucket.disk_failover_timeout

        self.config["spec"]["cluster"].update(
            {
                "autoFailoverMaxCount": 1,
                "autoFailoverServerGroup": bool(enabled),
                "autoFailoverOnDataDiskIssues": bool(enabled),
                "autoFailoverOnDataDiskIssuesTimePeriod": f"{disk_failover_timeout}s",
                "autoFailoverTimeout": f"{failover_timeouts[-1]}s",
            }
        )

    def set_cpu_settings(self):
        server_groups = self.config["spec"]["servers"]
        online_vcpus = self.test_config.cluster.online_cores * 2
        for server_group in server_groups:
            server_group.update({"resources": {"limits": {"cpu": online_vcpus}}})

    def set_memory_settings(self):
        if not self.test_config.cluster.kernel_mem_limit:
            return

        tune_services = set()
        # CAO uses different service names than perfrunner
        for service in self.test_config.cluster.kernel_mem_limit_services:
            if service == "kv":
                service = "data"
            elif service == "n1ql":
                service = "query"
            elif service == "fts":
                service = "search"
            elif service == "cbas":
                service = "analytics"
            tune_services.add(service)

        server_groups = self.config["spec"]["servers"]
        kernel_memory = self.test_config.cluster.kernel_mem_limit
        for server_group in server_groups:
            services_in_group = set(server_group["services"])
            if services_in_group.intersection(tune_services) and kernel_memory != 0:
                server_group.update({"resources": {"limits": {"memory": f"{kernel_memory}Mi"}}})

    def configure_autoscaling(self, sever_group: str):
        server_groups = self.config["spec"]["servers"]
        for server_group in server_groups:
            if server_group["name"] == server_group:
                server_group.update({"autoscaleEnabled": True})

    @supported_for(since=(2, 6, 0), feature="CNG")
    def set_cng_version(self, cng_tag: str):
        self.config["spec"]["networking"].update(
            {
                "cloudNativeGateway": {
                    "image": cng_tag,
                }
            }
        )


class CAOCouchbaseBucketFile(CAOFiles):
    """Couchbase bucket configuration."""

    def __init__(self, bucket_name: str):
        super().__init__("bucket_template", "")
        self.dest_file = f"cloud/operator/{bucket_name}.yaml"
        self.bucket_name = bucket_name

    def set_bucket_settings(self, bucket_quota: int, bucket_settings: BucketSettings):
        self.config["metadata"]["name"] = self.bucket_name
        self.config["spec"].update(
            {
                "memoryQuota": f"{bucket_quota}Mi",
                "replicas": bucket_settings.replica_number,
                "evictionPolicy": bucket_settings.eviction_policy,
                "compressionMode": bucket_settings.compression_mode or "off",
                "conflictResolution": bucket_settings.conflict_resolution_type or "seqno",
            }
        )


class CAOCouchbaseBackupFile(CAOFiles):
    """Backup configuration."""

    def __init__(self):
        super().__init__("backup_template", "")

    def set_schedule_time(self, cron_schedule: str):
        self.config["spec"]["full"]["schedule"] = cron_schedule


class CAOHorizontalAutoscalerFile(CAOFiles):
    """Horizontal pod autoscaler configuration."""

    def __init__(self):
        super().__init__("autoscaler_template", "")

    def setup_pod_autoscaler(
        self,
        cluster_name: str,
        server_group: str,
        min_nodes: int,
        max_nodes: int,
        target_metric: str,
        target_type: str,
        target_value: str,
    ):
        self.config["spec"]["scaleTargetRef"]["name"] = f"{server_group}.{cluster_name}"
        self.config["spec"]["minReplicas"] = min_nodes
        self.config["spec"]["maxReplicas"] = max_nodes
        self.config["spec"]["metrics"] = [
            {
                "type": "Pods",
                "pods": {
                    "metric": {"name": target_metric},
                    "target": {"type": target_type, target_type: target_value},
                },
            }
        ]


class CAOWorkerFile(CAOFiles):
    """Worker pods configuration."""

    def __init__(self, cluster_spec: ClusterSpec):
        super().__init__("worker_template", "")
        self.cluster_spec = cluster_spec

    def update_worker_spec(self):
        """Configure replica and resource limits."""
        # Update worker pods replicas
        self.config["spec"]["replicas"] = len(self.cluster_spec.workers)

        # Update worker pods resource limits
        k8s = self.cluster_spec.infrastructure_section("k8s")
        self.config["spec"]["template"]["spec"]["containers"][0].update(
            {
                "resources": {
                    "limits": {
                        "cpu": k8s.get("worker_cpu_limit", 80),
                        "memory": f"{k8s.get('worker_mem_limit', 128)}Gi",
                    }
                }
            }
        )


class CAOSyncgatewayDeploymentFile(CAOFiles):
    """Deployment setup for syncgateway.

    It configures:
    - Syncgateway bootstrap configuration as a k8s secret
    - Syncgateway deployment
    - Syncgateway service
    """

    def __init__(self, syncgateway_image: str, node_count: int):
        super().__init__("syncgateway_template", "")
        self.syncgateway_image = syncgateway_image
        self.node_count = node_count

    def configure_sgw(self):
        for config in self.all_configs:
            if config["kind"] == "Deployment":
                config["spec"]["template"]["spec"]["containers"][0][
                    "image"
                ] = self.syncgateway_image
                config["spec"]["replicas"] = self.node_count


class TimeTrackingFile(ConfigFile):
    """File for tracking time taken for resource management operations during a test.

    'Resource management operations' include, but aren't limited to:
      - Capella cluster deployment
      - Bucket creation
      - App Services deployment
    """

    def __init__(self):
        super().__init__("timings.json", FileType.JSON)