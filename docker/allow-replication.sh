#!/bin/sh
# Local development only: let the replica container connect for streaming replication.
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
