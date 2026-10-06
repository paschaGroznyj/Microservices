"""
tools/graph/tool_knowledge_graph.py
───────────────────────────────────
Тулза для агента: возвращает JSON-структуру графа знаний (узлы, рёбра, wiki-связи).
Возвращает ровно то, что нужно vis-network — без клиентской обработки.

Подключение в langchain_agent.py:
    from tools.graph.tool_knowledge_graph import make_knowledge_graph_tool
    knowledge_graph_tool = make_knowledge_graph_tool(agent)
    # добавить в список tools
"""

import logging
import json
from langchain.tools import tool

log = logging.getLogger(__name__)

# SQL-запрос возвращает один JSON-объект: { nodes: [], edges: [], stats: {} }
GRAPH_SQL = """
WITH
  k_nodes AS (
    SELECT
      jsonb_build_object(
        'id',          k.id,
        'label',       'k' || k.id,
        'title',       LEFT(k.text_knowledge, 80),
        'group',       COALESCE(k.provenance, 'agent-inference'),
        'value',       k.importance,
        'shape',       'dot',
        'type',        'knowledge',
        'importance',  k.importance,
        'provenance',  COALESCE(k.provenance, 'agent-inference'),
        'source',      k.metadata_knowledge->>'source',
        'text_full',   k.text_knowledge,
        'access_count', COALESCE(k.access_count, 0),
        'status',      COALESCE(k.status, 'active')
      ) AS node
    FROM knowledge k
    WHERE k.status = 'active' OR k.status IS NULL
  ),
  w_nodes AS (
    SELECT
      jsonb_build_object(
        'id',         'w' || w.id,
        'label',      w.title,
        'title',      w.title,
        'group',      'wiki',
        'value',      w.importance,
        'shape',      'star',
        'type',       'wiki',
        'importance', w.importance,
        'source_ids', w.source_ids,
        'text_full',  w.content_md,
        'links',      w.links,
        'status',     w.status
      ) AS node
    FROM wiki_pages w
    WHERE w.status IN ('published', 'draft', 'needs_update')
  ),
  k_edges AS (
    SELECT
      jsonb_build_object(
        'from',   er.source_id,
        'to',     er.target_id,
        'label',  er.relation_type,
        'value',  er.relevance_score,
        'width',  er.relevance_score / 2,
        'arrows', 'to',
        'type',   'relation',
        'dashes', false
      ) AS edge
    FROM entity_relations er
    JOIN knowledge ks ON ks.id = er.source_id
    JOIN knowledge kt ON kt.id = er.target_id
    WHERE (ks.status = 'active' OR ks.status IS NULL)
      AND (kt.status = 'active' OR kt.status IS NULL)
  ),
  w_edges AS (
    SELECT jsonb_build_object(
          'from',   'w' || w.id,
          'to',     je.source_id::int,
          'width',  2,
          'type',   'wiki_link',
          'dashes', true
        ) AS edge
        FROM wiki_pages w
        CROSS JOIN LATERAL jsonb_array_elements_text(w.source_ids) AS je(source_id)
        WHERE w.status IN ('published', 'draft', 'needs_update')
        AND jsonb_typeof(w.source_ids) = 'array'
      )
SELECT jsonb_build_object(
  'nodes', COALESCE(
    (SELECT jsonb_agg(node) FROM (
      SELECT node FROM k_nodes
      UNION ALL
      SELECT node FROM w_nodes
    ) all_nodes),
    '[]'::jsonb
  ),
  'edges', COALESCE(
    (SELECT jsonb_agg(edge) FROM (
      SELECT edge FROM k_edges
      UNION ALL
      SELECT edge FROM w_edges
    ) all_edges),
    '[]'::jsonb
  ),
  'stats', jsonb_build_object(
    'knowledge_count', (SELECT COUNT(*) FROM knowledge WHERE status = 'active' OR status IS NULL),
    'relations_count', (SELECT COUNT(*) FROM entity_relations er
                        JOIN knowledge ks ON ks.id = er.source_id
                        JOIN knowledge kt ON kt.id = er.target_id
                        WHERE (ks.status = 'active' OR ks.status IS NULL)
                          AND (kt.status = 'active' OR kt.status IS NULL)),
    'wiki_count', (SELECT COUNT(*) FROM wiki_pages WHERE status IN ('published', 'draft', 'needs_update'))
  )
) AS graph_json;
"""


def make_knowledge_graph_tool(agent):

    @tool
    async def get_knowledge_graph() -> str:
        """
        Возвращает JSON-структуру графа знаний (узлы, рёбра, wiki-страницы).
        Используется для визуализации базы знаний по запросу пользователя.

        Шаблон-ориентир по пути tools/make_graph/sample.html
        """
        try:
            async with agent.db.readonly_pool.acquire() as conn:
                row = await conn.fetchrow(GRAPH_SQL)
                return row['graph_json'] if row else "{}"
        except Exception as e:
            log.error("get_knowledge_graph failed: %s", e)
            return json.dumps({"error": str(e)})

    return get_knowledge_graph