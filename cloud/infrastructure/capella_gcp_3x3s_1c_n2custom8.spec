[infrastructure]
provider = capella
backend = gcp

[clusters]
couchbase1 =
	gce.gce_cluster_1.gce_node_group_1.1:kv
	gce.gce_cluster_1.gce_node_group_1.2:kv
	gce.gce_cluster_1.gce_node_group_1.3:kv
couchbase2 =
	gce.gce_cluster_2.gce_node_group_2.1:kv
	gce.gce_cluster_2.gce_node_group_2.2:kv
	gce.gce_cluster_2.gce_node_group_2.3:kv

[clients]
workers1 =
	gce.gce_cluster_1.gce_node_group_3.1

[utilities]
brokers1 = gce.gce_cluster_1.gce_node_group_4.1

[gce]
clusters = gce_cluster_1,gce_cluster_2

[gce_cluster_1]
node_groups = gce_node_group_1,gce_node_group_3,gce_node_group_4
storage_class = pd-ssd

[gce_cluster_2]
node_groups = gce_node_group_2
storage_class = pd-ssd

[gce_node_group_1]
instance_type = n2-custom-8-16384
instance_capacity = 3
volume_size = 300
volume_type = pd-ssd

[gce_node_group_2]
instance_type = n2-custom-8-16384
instance_capacity = 3
volume_size = 300
volume_type = pd-ssd

[gce_node_group_3]
instance_type = n2-standard-64
instance_capacity = 1
volume_size = 100
volume_type = pd-ssd

[gce_node_group_4]
instance_type = n2-standard-32
instance_capacity = 1

[storage]
data = /data

[credentials]
rest = Administrator:Password123!
ssh = root:couchbase

[parameters]
OS = Ubuntu 20.04
CPU = n2-custom-8-16384 (8 vCPU)
Memory = 16 GB
Disk = pd-ssd, 300GB, 15000 IOPS