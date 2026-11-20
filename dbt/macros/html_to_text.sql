{#-
  Plain text from an HTML fragment, in Trino SQL (used for FCDO travel advice):
    1. block ends (<br>, </p>, </li>, </h1-6>, </tr>, </div>, </ul>, </ol>) become line breaks
    2. all remaining tags are removed
    3. numeric entities (&#NNN;) and the common named entities below are decoded; &amp; is
       decoded last, so "&amp;lt;" becomes the text "&lt;" instead of being decoded twice
    4. non-breaking spaces (U+00A0) become spaces; runs of spaces and tabs collapse to one
       space, spaces around line breaks are removed,
       three or more line breaks become one blank line, and the result is trimmed
  Limits: tables and lists lose their structure, link targets are dropped, only the entities
  listed here are decoded, and comments or <script> blocks are not expected (GOV.UK travel
  advice has none). Phase 2 AI works on the resulting full text.
-#}
{% macro html_to_text(html) -%}
  {#- Named entities, decoded in one regexp_replace with a lookup map (&amp; separately, last). -#}
  {%- set named_entities = [
      ("nbsp", " "), ("lt", "<"), ("gt", ">"), ("quot", '"'), ("apos", "''"),
      ("rsquo", "’"), ("lsquo", "‘"), ("rdquo", "”"), ("ldquo", "“"),
      ("ndash", "–"), ("mdash", "—"), ("hellip", "…"),
  ] -%}
  {%- set entity_names = named_entities | map(attribute=0) | join("|") -%}
  {%- set entity_map -%}
    map(array[{% for name, _ in named_entities %}'{{ name }}'{{ ", " if not loop.last }}{% endfor %}],
        array[{% for _, text in named_entities %}'{{ text }}'{{ ", " if not loop.last }}{% endfor %}])
  {%- endset -%}
  {%- set ns = namespace(expr=html) -%}
  {%- set block_ends = "'(?i)<br\\s*/?>|</(p|li|h[1-6]|tr|div|ul|ol)>'" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", " ~ block_ends ~ ", chr(10))" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", '<[^>]+>', '')" -%}
  {%- set decode_numeric = "x -> chr(cast(x[1] as integer))" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", '&#([0-9]+);', " ~ decode_numeric ~ ")" -%}
  {%- set entity_pattern = "'&(" ~ entity_names ~ ");'" -%}
  {%- set decode_named = "e -> element_at(" ~ entity_map ~ ", e[1])" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", " ~ entity_pattern
      ~ ", " ~ decode_named ~ ")" -%}
  {%- set ns.expr = "replace(" ~ ns.expr ~ ", '&amp;', '&')" -%}
  {#- Non-breaking spaces (U+00A0) appear as literal characters, not only as &nbsp;. -#}
  {%- set ns.expr = "replace(" ~ ns.expr ~ ", chr(160), ' ')" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", '[ \\t]+', ' ')" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", ' ?\\n ?', chr(10))" -%}
  {%- set ns.expr = "regexp_replace(" ~ ns.expr ~ ", '\\s*\\n\\s*\\n\\s*', chr(10) || chr(10))" -%}
  trim({{ ns.expr }})
{%- endmacro %}
