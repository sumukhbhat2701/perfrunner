[clusters]
hebe_1 =
    172.23.100.190:kv,index,n1ql
    172.23.100.191:kv,index,n1ql
    172.23.100.192:kv,index,n1ql

[sync_gateways]
sync_gateways =
    172.23.100.193

[clients]
hosts =
    172.23.97.250
    172.23.97.251
    172.23.97.252
    172.23.97.253
    172.23.98.8
    172.23.100.194
credentials = root:couchbase

[storage]
data = /data
index = /data

[credentials]
rest = Administrator:password
ssh = root:couchbase

[parameters]
OS = CentOS 7
CPU = E5-2680 v3 (48 vCPU)
Memory = 64GB
Disk = Samsung Pro 850