# The init scripts are baked in rather than bind-mounted.
#
# On an SELinux-enforcing host the repository is labelled user_home_t, which
# container_t cannot read: the upstream entrypoint dies on `ls
# /docker-entrypoint-initdb.d/` with "Permission denied" and Postgres never
# boots. The usual workaround is a `:z`/`:Z` mount, but that relabels the host
# source tree as a side effect. COPY puts the files in an image layer
# (container_file_t), so nothing on the host is touched and SELinux stays
# enforcing.
FROM timescale/timescaledb:2.17.2-pg16

COPY init/ /docker-entrypoint-initdb.d/
