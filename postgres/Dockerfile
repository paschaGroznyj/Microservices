FROM pgvector/pgvector:pg15

ARG SQL_SCHEMA_DIR=/opt/postgres-schema/sql
ENV SQL_SCHEMA_DIR=${SQL_SCHEMA_DIR}

COPY sql/ ${SQL_SCHEMA_DIR}/
COPY apply-schema.sh /usr/local/bin/apply-schema.sh
COPY docker-entrypoint-wrapper.sh /usr/local/bin/docker-entrypoint-wrapper.sh

RUN chmod +x /usr/local/bin/apply-schema.sh /usr/local/bin/docker-entrypoint-wrapper.sh \
    && ln -s /usr/local/bin/apply-schema.sh /docker-entrypoint-initdb.d/00-apply-schema.sh

ENTRYPOINT ["docker-entrypoint-wrapper.sh"]
CMD ["postgres"]
