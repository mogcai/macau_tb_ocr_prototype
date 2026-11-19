# schema_meta
| Field | Type | Nullable | Key | Default | Index | Extra |
| ----- | ---- | -------- | --- | ------- | :---: | ----- |
| key   | TEXT | YES      | PRI | NULL    |   X   |       |
| value | TEXT | NO       |     |         |       |       |

# runs
|          Field           |     Type      | Nullable | Key | Default | Index | Extra |
| ------------------------ | ------------- | -------- | --- | ------- | :---: | ----- |
| run_id                   | TEXT          | YES      | PRI | NULL    |   X   |       |
| run_dir                  | TEXT          | NO       |     |         |       |       |
| source_pdf               | TEXT          | YES      |     | NULL    |       |       |
| source_pdf_sha256        | TEXT          | YES      |     | NULL    |       |       |
| source_pdf_sha256_source | TEXT          | YES      |     | NULL    |       |       |
| app_version              | TEXT          | YES      |     | NULL    |       |       |
| imported_at              | TEXT          | NO       |     |         |       |       |
| tb_page_count            | INTEGER       | NO       |     | 0       |       |       |
| line_item_count          | INTEGER       | NO       |     | 0       |       |       |
| review_row_count         | INTEGER       | NO       |     | 0       |       |       |
| is_published             | INTEGER       | NO       |     | 0       |       |       |
| CHECK                    | (is_published | YES      |     | NULL    |       |       |
| 1))                      |               | YES      |     | NULL    |       |       |
| published_at             | TEXT          | YES      |     | NULL    |       |       |
| published_by             | TEXT          | YES      |     | NULL    |       |       |
| config_json              | TEXT          | YES      |     | NULL    |       |       |
| run_report_json          | TEXT          | YES      |     | NULL    |       |       |

# banks
|      Field      |       Type       | Nullable | Key |   Default   | Index |     Extra     |
| --------------- | ---------------- | -------- | --- | ----------- | :---: | ------------- |
| bank_id         | INTEGER          | YES      | PRI | NULL        |   X   | AUTOINCREMENT |
| bank_key        | TEXT             | NO       | UNI |             |   X   |               |
| display_name    | TEXT             | NO       |     |             |       |               |
| normalized_name | TEXT             | NO       |     |             |       |               |
| canonical_zh    | TEXT             | YES      |     | NULL        |       |               |
| canonical_pt    | TEXT             | YES      |     | NULL        |       |               |
| canonical_en    | TEXT             | YES      |     | NULL        |       |               |
| entity_type     | TEXT             | NO       |     | bank        |       |               |
| CHECK           | (entity_type     | YES      |     | NULL        |       |               |
| branch          |                  | YES      |     | NULL        |       |               |
| group           |                  | YES      |     | NULL        |       |               |
| other           |                  | YES      |     | NULL        |       |               |
| unknown         | ))               | YES      |     | NULL        |       |               |
| registry_status | TEXT             | NO       |     | provisional |       |               |
| CHECK           | (registry_status | YES      |     | NULL        |       |               |
| provisional     |                  | YES      |     | NULL        |       |               |
| pending         |                  | YES      |     | NULL        |       |               |
| rejected        | ))               | YES      |     | NULL        |       |               |
| first_seen_run  | TEXT             | YES      |     | NULL        |       |               |
| notes           | TEXT             | YES      |     | NULL        |       |               |

# bank_aliases
|     Field      | Type | Nullable | Key | Default | Index | Extra |
| -------------- | ---- | -------- | --- | ------- | :---: | ----- |
| alias_key      | TEXT | YES      | PRI | NULL    |   X   |       |
| bank_key       | TEXT | NO       |     |         |       |       |
| alias_name     | TEXT | NO       |     |         |       |       |
| canonical_name | TEXT | NO       |     |         |       |       |
| note           | TEXT | YES      |     | NULL    |       |       |

# statements
|          Field          |  Type   | Nullable | Key | Default | Index | Extra |
| ----------------------- | ------- | -------- | --- | ------- | :---: | ----- |
| statement_id            | TEXT    | YES      | PRI | NULL    |   X   |       |
| run_id                  | TEXT    | NO       |     |         |       |       |
| paper_key               | TEXT    | NO       |     |         |   X   |       |
| publication_date        | TEXT    | YES      |     | NULL    |       |       |
| publication_date_source | TEXT    | NO       |     |         |       |       |
| statement_date          | TEXT    | YES      |     | NULL    |       |       |
| statement_date_source   | TEXT    | NO       |     |         |       |       |
| bank_id                 | INTEGER | YES      |     | NULL    |       |       |
| bank_key                | TEXT    | YES      |     | NULL    |       |       |
| bank_name_raw           | TEXT    | YES      |     | NULL    |       |       |
| bank_name_alternatives  | TEXT    | YES      |     | NULL    |       |       |
| bank_name_source        | TEXT    | YES      |     | NULL    |       |       |
| pdf_pages               | TEXT    | NO       |     |         |       |       |
| printed_pages           | TEXT    | YES      |     | NULL    |       |       |
| page_count              | INTEGER | NO       |     |         |       |       |
| metadata_status         | TEXT    | YES      |     | NULL    |       |       |
| identity_status         | TEXT    | NO       |     |         |       |       |
| row_count               | INTEGER | NO       |     | 0       |       |       |
| review_row_count        | INTEGER | NO       |     | 0       |       |       |
| UNIQUE                  | (run_id | YES      |     | NULL    |       |       |
| paper_key)              |         | YES      |     | NULL    |   X   |       |

# pages
|          Field           |  Type   | Nullable | Key | Default | Index |     Extra     |
| ------------------------ | ------- | -------- | --- | ------- | :---: | ------------- |
| page_id                  | INTEGER | YES      | PRI | NULL    |   X   | AUTOINCREMENT |
| run_id                   | TEXT    | NO       |     |         |       |               |
| pdf_page                 | INTEGER | NO       |     |         |       |               |
| printed_page             | TEXT    | YES      |     | NULL    |       |               |
| statement_id             | TEXT    | YES      |     | NULL    |   X   |               |
| bank_name_raw            | TEXT    | YES      |     | NULL    |       |               |
| publication_date         | TEXT    | YES      |     | NULL    |       |               |
| statement_date           | TEXT    | YES      |     | NULL    |       |               |
| metadata_status          | TEXT    | YES      |     | NULL    |       |               |
| ocr_status               | TEXT    | YES      |     | NULL    |       |               |
| ocr_languages            | TEXT    | YES      |     | NULL    |       |               |
| image_path               | TEXT    | YES      |     | NULL    |       |               |
| image_width              | INTEGER | YES      |     | NULL    |       |               |
| image_height             | INTEGER | YES      |     | NULL    |       |               |
| number_format_detected   | TEXT    | YES      |     | NULL    |       |               |
| number_format_confidence | REAL    | YES      |     | NULL    |       |               |
| document_json            | TEXT    | YES      |     | NULL    |       |               |
| UNIQUE                   | (run_id | YES      |     | NULL    |       |               |
| pdf_page)                |         | YES      |     | NULL    |       |               |

# line_items
|           Field            |        Type         | Nullable | Key | Default | Index |     Extra     |
| -------------------------- | ------------------- | -------- | --- | ------- | :---: | ------------- |
| line_item_id               | INTEGER             | YES      | PRI | NULL    |   X   | AUTOINCREMENT |
| statement_id               | TEXT                | NO       |     |         |   X   |               |
| run_id                     | TEXT                | NO       |     |         |       |               |
| row_order                  | INTEGER             | NO       |     |         |       |               |
| pdf_page                   | INTEGER             | NO       |     |         |       |               |
| printed_page               | TEXT                | YES      |     | NULL    |       |               |
| bank_name_raw              | TEXT                | YES      |     | NULL    |       |               |
| label_raw                  | TEXT                | NO       |     |         |       |               |
| label_normalized           | TEXT                | YES      |     | NULL    |       |               |
| label_standard             | TEXT                | YES      |     | NULL    |   X   |               |
| label_standard_id          | TEXT                | YES      |     | NULL    |       |               |
| label_standard_status      | TEXT                | YES      |     | NULL    |       |               |
| label_standard_score       | REAL                | YES      |     | NULL    |       |               |
| label_role                 | TEXT                | YES      |     | NULL    |       |               |
| parent_label_raw           | TEXT                | YES      |     | NULL    |       |               |
| parent_label_standard      | TEXT                | YES      |     | NULL    |   X   |               |
| label_path                 | TEXT                | YES      |     | NULL    |   X   |               |
| label_language             | TEXT                | YES      |     | NULL    |       |               |
| label_path_key             | TEXT                | NO       |     |         |   X   |               |
| occurrence                 | INTEGER             | NO       |     |         |   X   |               |
| natural_key                | TEXT                | NO       |     |         |       |               |
| natural_key_status         | TEXT                | NO       |     |         |       |               |
| CHECK                      | (natural_key_status | YES      |     | NULL    |       |               |
| duplicate                  |                     | YES      |     | NULL    |       |               |
| missing_label              | ))                  | YES      |     | NULL    |       |               |
| debit_raw                  | TEXT                | YES      |     | NULL    |       |               |
| credit_raw                 | TEXT                | YES      |     | NULL    |       |               |
| debit_amount               | TEXT                | YES      |     | NULL    |       |               |
| credit_amount              | TEXT                | YES      |     | NULL    |       |               |
| debit_cents                | INTEGER             | YES      |     | NULL    |       |               |
| credit_cents               | INTEGER             | YES      |     | NULL    |       |               |
| number_format              | TEXT                | YES      |     | NULL    |       |               |
| number_format_confidence   | REAL                | YES      |     | NULL    |       |               |
| debit_parse_status         | TEXT                | YES      |     | NULL    |       |               |
| credit_parse_status        | TEXT                | YES      |     | NULL    |       |               |
| review_reason              | TEXT                | YES      |     | NULL    |       |               |
| review_status              | TEXT                | YES      |     | NULL    |       |               |
| line_item_concept_id       | INTEGER             | YES      |     | NULL    |   X   |               |
| mapping_status             | TEXT                | YES      |     | NULL    |       |               |
| mapping_version            | INTEGER             | YES      |     | NULL    |       |               |
| row_confidence             | REAL                | YES      |     | NULL    |       |               |
| row_average_confidence     | REAL                | YES      |     | NULL    |       |               |
| source_image               | TEXT                | YES      |     | NULL    |       |               |
| bbox_x0                    | REAL                | YES      |     | NULL    |       |               |
| bbox_y0                    | REAL                | YES      |     | NULL    |       |               |
| bbox_x1                    | REAL                | YES      |     | NULL    |       |               |
| bbox_y1                    | REAL                | YES      |     | NULL    |       |               |
| user_standard_label_id     | INTEGER             | YES      |     | NULL    |       |               |
| user_standard_match_status | TEXT                | YES      |     | NULL    |       |               |
| UNIQUE                     | (statement_id       | YES      |     | NULL    |   X   |               |
| row_order)                 |                     | YES      |     | NULL    |       |               |

# row_verification
|    Field     |           Type           | Nullable | Key | Default | Index | Extra |
| ------------ | ------------------------ | -------- | --- | ------- | :---: | ----- |
| line_item_id | INTEGER                  | YES      | PRI | NULL    |   X   |       |
| REFERENCES   | line_items(line_item_id) | YES      |     | NULL    |       |       |
| status       | TEXT                     | NO       |     |         |       |       |
| CHECK        | (status                  | YES      |     | NULL    |       |       |
| rejected     |                          | YES      |     | NULL    |       |       |
| pending      | ))                       | YES      |     | NULL    |       |       |
| verified_at  | TEXT                     | YES      |     | NULL    |       |       |
| verified_by  | TEXT                     | YES      |     | NULL    |       |       |
| note         | TEXT                     | YES      |     | NULL    |       |       |

# bank_name_aliases
|      Field       |    Type     | Nullable | Key | Default  | Index |     Extra     |
| ---------------- | ----------- | -------- | --- | -------- | :---: | ------------- |
| alias_id         | INTEGER     | YES      | PRI | NULL     |   X   | AUTOINCREMENT |
| bank_id          | INTEGER     | NO       |     |          |       |               |
| raw_name         | TEXT        | NO       |     |          |       |               |
| normalized_name  | TEXT        | NO       |     |          |   X   |               |
| language         | TEXT        | YES      |     | NULL     |       |               |
| alias_type       | TEXT        | NO       |     | official |       |               |
| CHECK            | (alias_type | YES      |     | NULL     |       |               |
| branch           |             | YES      |     | NULL     |       |               |
| group            |             | YES      |     | NULL     |       |               |
| historical       |             | YES      |     | NULL     |       |               |
| ocr_variant      |             | YES      |     | NULL     |       |               |
| combined         |             | YES      |     | NULL     |       |               |
| other            | ))          | YES      |     | NULL     |       |               |
| status           | TEXT        | NO       |     | pending  |   X   |               |
| CHECK            | (status     | YES      |     | NULL     |       |               |
| pending          |             | YES      |     | NULL     |       |               |
| rejected         | ))          | YES      |     | NULL     |       |               |
| source           | TEXT        | YES      |     | NULL     |       |               |
| note             | TEXT        | YES      |     | NULL     |       |               |
| UNIQUE           | (bank_id    | YES      |     | NULL     |   X   |               |
| normalized_name) |             | YES      |     | NULL     |   X   |               |

# line_item_concepts
|         Field          |  Type   | Nullable | Key | Default  | Index |     Extra     |
| ---------------------- | ------- | -------- | --- | -------- | :---: | ------------- |
| concept_id             | INTEGER | YES      | PRI | NULL     |   X   | AUTOINCREMENT |
| concept_key            | TEXT    | NO       | UNI |          |   X   |               |
| canonical_zh           | TEXT    | YES      |     | NULL     |       |               |
| canonical_pt           | TEXT    | YES      |     | NULL     |       |               |
| canonical_en           | TEXT    | YES      |     | NULL     |       |               |
| parent_concept_id      | INTEGER | YES      |     | NULL     |       |               |
| level                  | TEXT    | NO       |     | parent   |       |               |
| CHECK                  | (level  | YES      |     | NULL     |       |               |
| child                  | ))      | YES      |     | NULL     |       |               |
| category               | TEXT    | YES      |     | NULL     |       |               |
| status                 | TEXT    | NO       |     | approved |       |               |
| CHECK                  | (status | YES      |     | NULL     |       |               |
| provisional            |         | YES      |     | NULL     |       |               |
| pending                |         | YES      |     | NULL     |       |               |
| merged                 | ))      | YES      |     | NULL     |       |               |
| merged_into_concept_id | INTEGER | YES      |     | NULL     |       |               |
| source                 | TEXT    | YES      |     | NULL     |       |               |
| note                   | TEXT    | YES      |     | NULL     |       |               |

# line_item_aliases
|      Field       |              Type              | Nullable | Key | Default  | Index |     Extra     |
| ---------------- | ------------------------------ | -------- | --- | -------- | :---: | ------------- |
| alias_id         | INTEGER                        | YES      | PRI | NULL     |   X   | AUTOINCREMENT |
| concept_id       | INTEGER                        | NO       |     |          |       |               |
| REFERENCES       | line_item_concepts(concept_id) | YES      |     | NULL     |       |               |
| raw_name         | TEXT                           | NO       |     |          |       |               |
| normalized_name  | TEXT                           | NO       |     |          |   X   |               |
| language         | TEXT                           | YES      |     | NULL     |   X   |               |
| alias_type       | TEXT                           | NO       |     | official |       |               |
| CHECK            | (alias_type                    | YES      |     | NULL     |       |               |
| ocr_variant      |                                | YES      |     | NULL     |       |               |
| abbreviation     |                                | YES      |     | NULL     |       |               |
| historical       |                                | YES      |     | NULL     |       |               |
| translation      |                                | YES      |     | NULL     |       |               |
| other            | ))                             | YES      |     | NULL     |       |               |
| status           | TEXT                           | NO       |     | pending  |   X   |               |
| CHECK            | (status                        | YES      |     | NULL     |       |               |
| pending          |                                | YES      |     | NULL     |       |               |
| rejected         | ))                             | YES      |     | NULL     |       |               |
| source           | TEXT                           | YES      |     | NULL     |       |               |
| note             | TEXT                           | YES      |     | NULL     |       |               |
| UNIQUE           | (concept_id                    | YES      |     | NULL     |   X   |               |
| normalized_name) |                                | YES      |     | NULL     |       |               |

# name_mapping_review
|        Field         |     Type     | Nullable | Key | Default | Index |     Extra     |
| -------------------- | ------------ | -------- | --- | ------- | :---: | ------------- |
| review_id            | INTEGER      | YES      | PRI | NULL    |   X   | AUTOINCREMENT |
| entity_type          | TEXT         | NO       |     |         |   X   |               |
| CHECK                | (entity_type | YES      |     | NULL    |       |               |
| line_item            | ))           | YES      |     | NULL    |       |               |
| raw_name             | TEXT         | NO       |     |         |       |               |
| normalized_name      | TEXT         | NO       |     |         |       |               |
| language             | TEXT         | YES      |     | NULL    |       |               |
| current_entity_id    | INTEGER      | YES      |     | NULL    |       |               |
| suggested_entity_id  | INTEGER      | YES      |     | NULL    |       |               |
| suggested_canonical  | TEXT         | YES      |     | NULL    |       |               |
| match_method         | TEXT         | YES      |     | NULL    |       |               |
| match_score          | REAL         | YES      |     | NULL    |       |               |
| occurrence_count     | INTEGER      | NO       |     | 0       |   X   |               |
| source_issues        | TEXT         | YES      |     | NULL    |       |               |
| source_pages         | TEXT         | YES      |     | NULL    |       |               |
| status               | TEXT         | NO       |     | pending |   X   |               |
| CHECK                | (status      | YES      |     | NULL    |       |               |
| approved             |              | YES      |     | NULL    |       |               |
| rejected             | ))           | YES      |     | NULL    |       |               |
| reviewed_by          | TEXT         | YES      |     | NULL    |       |               |
| reviewed_at          | TEXT         | YES      |     | NULL    |       |               |
| note                 | TEXT         | YES      |     | NULL    |       |               |
| UNIQUE               | (entity_type | YES      |     | NULL    |       |               |
| normalized_name      |              | YES      |     | NULL    |       |               |
| suggested_entity_id) |              | YES      |     | NULL    |       |               |

# name_mapping_audit
|     Field     |  Type   | Nullable | Key | Default | Index |     Extra     |
| ------------- | ------- | -------- | --- | ------- | :---: | ------------- |
| audit_id      | INTEGER | YES      | PRI | NULL    |   X   | AUTOINCREMENT |
| entity_type   | TEXT    | NO       |     |         |       |               |
| raw_name      | TEXT    | NO       |     |         |       |               |
| action        | TEXT    | NO       |     |         |       |               |
| old_entity_id | INTEGER | YES      |     | NULL    |       |               |
| new_entity_id | INTEGER | YES      |     | NULL    |       |               |
| actor         | TEXT    | YES      |     | NULL    |       |               |
| at            | TEXT    | YES      |     | NULL    |       |               |
| note          | TEXT    | YES      |     | NULL    |       |               |

# standard_labels
|       Field        |  Type   | Nullable | Key |   Default   | Index |     Extra     |
| ------------------ | ------- | -------- | --- | ----------- | :---: | ------------- |
| standard_id        | INTEGER | YES      | PRI | NULL        |   X   | AUTOINCREMENT |
| standard_key       | TEXT    | NO       | UNI |             |   X   |               |
| parent_standard_id | INTEGER | YES      |     | NULL        |       |               |
| canonical_zh       | TEXT    | YES      |     | NULL        |       |               |
| canonical_pt       | TEXT    | YES      |     | NULL        |       |               |
| canonical_en       | TEXT    | YES      |     | NULL        |       |               |
| level              | TEXT    | NO       |     | parent      |       |               |
| CHECK              | (level  | YES      |     | NULL        |       |               |
| child              | ))      | YES      |     | NULL        |       |               |
| category           | TEXT    | YES      |     | NULL        |       |               |
| status             | TEXT    | NO       |     | provisional |       |               |
| CHECK              | (status | YES      |     | NULL        |       |               |
| provisional        |         | YES      |     | NULL        |       |               |
| rejected           | ))      | YES      |     | NULL        |       |               |
| source             | TEXT    | YES      |     | NULL        |       |               |
| note               | TEXT    | YES      |     | NULL        |       |               |

# standard_label_aliases
|      Field      |             Type             | Nullable | Key |   Default   | Index |     Extra     |
| --------------- | ---------------------------- | -------- | --- | ----------- | :---: | ------------- |
| alias_id        | INTEGER                      | YES      | PRI | NULL        |   X   | AUTOINCREMENT |
| standard_id     | INTEGER                      | NO       |     |             |       |               |
| REFERENCES      | standard_labels(standard_id) | YES      |     | NULL        |       |               |
| raw_name        | TEXT                         | NO       |     |             |       |               |
| normalized_name | TEXT                         | NO       |     |             |       |               |
| language        | TEXT                         | YES      |     | NULL        |       |               |
| status          | TEXT                         | NO       |     | provisional |       |               |
| CHECK           | (status                      | YES      |     | NULL        |       |               |
| provisional     |                              | YES      |     | NULL        |       |               |
| rejected        | ))                           | YES      |     | NULL        |       |               |
| source          | TEXT                         | YES      |     | NULL        |       |               |
| note            | TEXT                         | YES      |     | NULL        |       |               |
| UNIQUE          | (standard_id                 | YES      |     | NULL        |       |               |
| normalized_name |                              | YES      |     | NULL        |       |               |
| language)       |                              | YES      |     | NULL        |       |               |
