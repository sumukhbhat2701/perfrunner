from datetime import timedelta
from urllib import parse

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.durability import DurabilityLevel, ServerDurability
from couchbase.management.collections import CollectionSpec
from couchbase.management.users import User
from couchbase.options import QueryOptions
from couchbase.views import ViewQuery
from txcouchbase.cluster import TxCluster

from spring.cbgen_helpers import backoff, quiet, time_all, timeit


class CBAsyncGen4:

    TIMEOUT = 120  # seconds

    def __init__(self, **kwargs):
        connstr = 'couchbase://{host}?'
        connstr = connstr.format(host=kwargs['host'])

        if kwargs["ssl_mode"] == 'n2n':
            connstr = connstr.replace('couchbase', 'couchbases')
            connstr += '&certpath=root.pem'

        self.cluster = TxCluster(
            connstr,
            authenticator=PasswordAuthenticator(kwargs['username'], kwargs['password']),
            kv_timeout=timedelta(seconds=self.TIMEOUT),
        )

        self.bucket_name = kwargs['bucket']
        self.collections = dict()
        self.collection = None

    def connect_collections(self, scope_collection_list):
        self.bucket = self.cluster.bucket(self.bucket_name)
        for scope_collection in scope_collection_list:
            scope, collection = scope_collection.split(":")
            if scope == "_default" and collection == "_default":
                self.collections[scope_collection] = \
                    self.bucket.default_collection()
            else:
                self.collections[scope_collection] = \
                    self.bucket.scope(scope).collection(collection)

    def create(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_upsert(*args[1:], **kwargs)

    def create_durable(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_upsert_durable(*args[1:], **kwargs)

    def read(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_read(*args[1:], **kwargs)

    def update(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_upsert(*args[1:], **kwargs)

    def update_durable(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_upsert_durable(*args[1:], **kwargs)

    def delete(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_delete(*args[1:], **kwargs)

    def do_upsert(self, key: str, doc: dict, persist_to: int = 0,
                  replicate_to: int = 0, ttl: int = 0):
        return self.collection.upsert(
            key, doc,
            expiry=timedelta(seconds=ttl),
            # CBPS-1027 discusses the reason for this, still need to figure out the cause
            # durability=ClientDurability(
            #     replicate_to=ReplicateTo(replicate_to),
            #     persist_to=PersistTo(persist_to)
            # )
        )

    def do_upsert_durable(self, key: str, doc: dict, durability: int = None, ttl: int = 0):
        return self.collection.upsert(
            key, doc,
            expiry=timedelta(seconds=ttl),
            durability=ServerDurability(DurabilityLevel(durability))
        )

    def do_read(self, key: str):
        return self.collection.get(key)

    def do_delete(self, key: str):
        return self.collection.remove(key)


class CBGen4(CBAsyncGen4):

    TIMEOUT = 600  # seconds
    N1QL_TIMEOUT = 600

    def __init__(self, ssl_mode: str = 'none', n1ql_timeout: int = None, **kwargs):
        connstr = 'couchbase://{host}?{params}'

        enable_tracing = kwargs["connstr_params"].pop('enable_tracing', 'false').lower() == 'true'

        connstr_params = parse.urlencode(kwargs["connstr_params"])

        if ssl_mode == 'data' or ssl_mode == 'n2n':
            connstr = connstr.replace('couchbase', 'couchbases')
            connstr += '&certpath=root.pem'

        connstr = connstr.format(host=kwargs['host'], params=connstr_params)

        self.cluster = Cluster(
            connstr,
            authenticator=PasswordAuthenticator(kwargs['username'], kwargs['password']),
            kv_timeout=timedelta(seconds=self.TIMEOUT),
            query_timeout=timedelta(seconds=n1ql_timeout if n1ql_timeout else self.N1QL_TIMEOUT),
            enable_tracing=enable_tracing
        )
        self.bucket_name = kwargs['bucket']
        self.bucket = None
        self.collections = dict()
        self.collection = None

    @quiet
    @backoff
    def do_create(self, *args, **kwargs):
        super().do_upsert(*args, **kwargs)

    @quiet
    @backoff
    def do_create_durable(self, *args, **kwargs):
        super().do_upsert_durable(*args, **kwargs)

    def get(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_get(*args[1:], **kwargs)

    def do_get(self, *args, **kwargs):
        return super().do_read(*args, **kwargs)

    @time_all
    def do_read(self, *args, **kwargs):
        super().do_read(*args, **kwargs)

    def set(self, *args, **kwargs):
        self.collection = self.collections[args[0]]
        return self.do_upsert(*args[1:], **kwargs)

    def do_set(self, *args, **kwargs):
        return super().do_upsert(*args, **kwargs)

    @time_all
    def do_upsert(self, *args, **kwargs):
        super().do_upsert(*args, **kwargs)

    @time_all
    def do_update_durable(self, *args, **kwargs):
        super().do_upsert_durable(*args, **kwargs)

    @quiet
    def do_delete(self, *args, **kwargs):
        super().do_delete(*args, **kwargs)

    @timeit
    def view_query(self, ddoc: str, view: str, query: ViewQuery):
        tuple(self.cluster.view_query(ddoc, view, query=query))

    @quiet
    @timeit
    def n1ql_query(self, n1ql_query: str, options: QueryOptions):
        tuple(self.cluster.query(n1ql_query, options))

    def create_user_manager(self):
        self.user_manager = self.cluster.users()

    def create_collection_manager(self):
        self.collection_manager = self.cluster.bucket(self.bucket_name).collections()

    @quiet
    @backoff
    def do_upsert_user(self, *args, **kwargs):
        return self.user_manager.upsert_user(
            User(username=args[0], roles=args[1], password=args[2])
        )

    def get_roles(self):
        return self.user_manager.get_roles()

    def do_collection_create(self, *args, **kwargs):
        self.collection_manager.create_collection(
            CollectionSpec(scope_name=args[0], collection_name=args[1])
        )

    def do_collection_drop(self, *args, **kwargs):
        self.collection_manager.drop_collection(
            CollectionSpec(scope_name=args[0], collection_name=args[1])
        )