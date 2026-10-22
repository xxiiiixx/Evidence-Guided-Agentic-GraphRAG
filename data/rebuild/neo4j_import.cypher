// ========================================================================================
// Day 3 重建图导入脚本
// ========================================================================================
// 输入：同目录的 nodes.csv / relationships.csv
//      （由 scripts/rebuild_graph.py 从 data/C9/cypher/ 生成，勿手工编辑）
//
// 与 data/C9/cypher/neo4j_import.cypher 的差别——只建该有的东西：
//   * 8 类节点：Recipe / Ingredient / CookingStep / RecipeCategory /
//              DifficultyLevel / Root / CookingMethod / CookingTool
//   * 4 类关系：REQUIRES / CONTAINS_STEP / BELONGS_TO / DIFFICULTY_LEVEL
//   * 不建 Category / ConceptType / GraphStatistics / RelationshipStatistics
//   * 不建 SIMILAR / USES_SAME_TOOL / USES_SAME_METHOD / NEXT_STEP / HAS_CONCEPT_TYPE
//     （原脚本用 LIMIT 1000 截断生成 14193 条，既不是真实数据也破坏连通性判定）
//   * category 已拆成 recipeCategory / ingredientCategory，不再跨 label 混用
//   * isMain（主料/辅料）从 Ingredient 节点搬到 REQUIRES 边上——主料/辅料是相对某道菜
//     而言的，同一样食材在 A 菜是主料、在 B 菜可能是辅料，属性本就该在边上。
//     relationships.csv 因此比原文件多一列 isMain（8 列），仅 REQUIRES 行有值。
//   * 不再需要 nodeId >= '200000000' 过滤：占位节点 200000000/300000000 已被删掉
//   * 不依赖 APOC：动态标签用 5 段显式语句代替，关系类型直接写死
//
// 用法：docker compose -f data/rebuild/docker-compose.yml up -d
// ========================================================================================

// ---------------------------------------------------------------------------------------
// 1. 清空。本实例专用于重建图，重跑脚本时整体重建，避免上一轮的残留关系。
// ---------------------------------------------------------------------------------------
MATCH (n) DETACH DELETE n;

// ---------------------------------------------------------------------------------------
// 2. 约束与索引
// ---------------------------------------------------------------------------------------
CREATE CONSTRAINT recipe_id_unique IF NOT EXISTS FOR (r:Recipe) REQUIRE r.nodeId IS UNIQUE;
CREATE CONSTRAINT ingredient_id_unique IF NOT EXISTS FOR (i:Ingredient) REQUIRE i.nodeId IS UNIQUE;
CREATE CONSTRAINT cookingstep_id_unique IF NOT EXISTS FOR (s:CookingStep) REQUIRE s.nodeId IS UNIQUE;
CREATE CONSTRAINT recipecategory_id_unique IF NOT EXISTS FOR (c:RecipeCategory) REQUIRE c.nodeId IS UNIQUE;
CREATE CONSTRAINT difficultylevel_id_unique IF NOT EXISTS FOR (d:DifficultyLevel) REQUIRE d.nodeId IS UNIQUE;
CREATE CONSTRAINT root_id_unique IF NOT EXISTS FOR (x:Root) REQUIRE x.nodeId IS UNIQUE;
CREATE CONSTRAINT cookingmethod_id_unique IF NOT EXISTS FOR (m:CookingMethod) REQUIRE m.nodeId IS UNIQUE;
CREATE CONSTRAINT cookingtool_id_unique IF NOT EXISTS FOR (t:CookingTool) REQUIRE t.nodeId IS UNIQUE;

CREATE INDEX recipe_name_index IF NOT EXISTS FOR (r:Recipe) ON (r.name);
CREATE INDEX recipe_difficulty_index IF NOT EXISTS FOR (r:Recipe) ON (r.difficulty);
CREATE INDEX recipe_cuisine_index IF NOT EXISTS FOR (r:Recipe) ON (r.cuisineType);
CREATE INDEX recipe_category_index IF NOT EXISTS FOR (r:Recipe) ON (r.recipeCategory);
CREATE INDEX ingredient_name_index IF NOT EXISTS FOR (i:Ingredient) ON (i.name);
CREATE INDEX ingredient_category_index IF NOT EXISTS FOR (i:Ingredient) ON (i.ingredientCategory);
CREATE INDEX cookingstep_number_index IF NOT EXISTS FOR (s:CookingStep) ON (s.stepNumber);
CREATE INDEX recipecategory_name_index IF NOT EXISTS FOR (c:RecipeCategory) ON (c.name);

// 全文索引名字与 C9 保持一致，代码里的检索不用改
CREATE FULLTEXT INDEX recipe_fulltext_index IF NOT EXISTS
FOR (r:Recipe) ON EACH [r.name, r.description, r.preferredTerm, r.fsn, r.tags];
CREATE FULLTEXT INDEX ingredient_fulltext_index IF NOT EXISTS
FOR (i:Ingredient) ON EACH [i.name, i.description, i.preferredTerm, i.fsn];
CREATE FULLTEXT INDEX cookingstep_fulltext_index IF NOT EXISTS
FOR (s:CookingStep) ON EACH [s.name, s.description, s.methods, s.tools];

// ---------------------------------------------------------------------------------------
// 3. 节点
//    空值统一写成 null（CASE 同时挡掉空串和字符串 'null'），不用空字符串占位。
// ---------------------------------------------------------------------------------------

// 菜谱 321
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'Recipe'
MERGE (r:Recipe {nodeId: row.nodeId})
SET r.name            = row.name,
    r.preferredTerm   = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    r.fsn             = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    r.conceptType     = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END,
    r.synonyms        = CASE WHEN row.synonyms IN ['', 'null'] THEN null ELSE row.synonyms END,
    r.difficulty      = CASE WHEN row.difficulty IN ['', 'null'] THEN null ELSE toFloat(row.difficulty) END,
    r.cuisineType     = CASE WHEN row.cuisineType IN ['', 'null'] THEN null ELSE row.cuisineType END,
    r.prepTime        = CASE WHEN row.prepTime IN ['', 'null'] THEN null ELSE row.prepTime END,
    r.cookTime        = CASE WHEN row.cookTime IN ['', 'null'] THEN null ELSE row.cookTime END,
    r.servings        = CASE WHEN row.servings IN ['', 'null'] THEN null ELSE row.servings END,
    r.tags            = CASE WHEN row.tags IN ['', 'null'] THEN null ELSE row.tags END,
    r.filePath        = CASE WHEN row.filePath IN ['', 'null'] THEN null ELSE row.filePath END,
    r.recipeCategory  = CASE WHEN row.recipeCategory IN ['', 'null'] THEN null ELSE row.recipeCategory END
RETURN count(r);

// 食材 746
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'Ingredient'
MERGE (i:Ingredient {nodeId: row.nodeId})
SET i.name               = row.name,
    i.preferredTerm      = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    i.conceptType        = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END,
    i.synonyms           = CASE WHEN row.synonyms IN ['', 'null'] THEN null ELSE row.synonyms END,
    i.ingredientCategory = CASE WHEN row.ingredientCategory IN ['', 'null'] THEN null ELSE row.ingredientCategory END,
    i.mergedCount        = CASE WHEN row.mergedCount IN ['', 'null'] THEN null ELSE toInteger(row.mergedCount) END,
    i.mergedAliases      = CASE WHEN row.mergedAliases IN ['', 'null'] THEN null ELSE row.mergedAliases END
RETURN count(i);

// 烹饪步骤 2507
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'CookingStep'
MERGE (s:CookingStep {nodeId: row.nodeId})
SET s.name          = row.name,
    s.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    s.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END,
    s.description   = CASE WHEN row.description IN ['', 'null'] THEN null ELSE row.description END,
    s.stepNumber    = CASE WHEN row.stepNumber IN ['', 'null'] THEN null ELSE toFloat(row.stepNumber) END,
    s.methods       = CASE WHEN row.methods IN ['', 'null'] THEN null ELSE row.methods END,
    s.tools         = CASE WHEN row.tools IN ['', 'null'] THEN null ELSE row.tools END,
    s.timeEstimate  = CASE WHEN row.timeEstimate IN ['', 'null'] THEN null ELSE row.timeEstimate END
RETURN count(s);

// 分类 13 / 难度 5 / schema 节点 3：字段相同（name, preferredTerm, fsn, conceptType）。
// 这里逐个 label 写死，不用 apoc.merge.node 的动态标签——整份脚本因此不依赖 APOC。
LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'RecipeCategory'
MERGE (c:RecipeCategory {nodeId: row.nodeId})
SET c.name          = row.name,
    c.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    c.fsn           = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    c.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END
RETURN count(c);

LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'DifficultyLevel'
MERGE (d:DifficultyLevel {nodeId: row.nodeId})
SET d.name          = row.name,
    d.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    d.fsn           = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    d.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END
RETURN count(d);

LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'Root'
MERGE (x:Root {nodeId: row.nodeId})
SET x.name          = row.name,
    x.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    x.fsn           = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    x.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END
RETURN count(x);

LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'CookingMethod'
MERGE (m:CookingMethod {nodeId: row.nodeId})
SET m.name          = row.name,
    m.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    m.fsn           = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    m.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END
RETURN count(m);

LOAD CSV WITH HEADERS FROM 'file:///nodes.csv' AS row
WITH row WHERE row.labels = 'CookingTool'
MERGE (t:CookingTool {nodeId: row.nodeId})
SET t.name          = row.name,
    t.preferredTerm = CASE WHEN row.preferredTerm IN ['', 'null'] THEN null ELSE row.preferredTerm END,
    t.fsn           = CASE WHEN row.fsn IN ['', 'null'] THEN null ELSE row.fsn END,
    t.conceptType   = CASE WHEN row.conceptType IN ['', 'null'] THEN null ELSE row.conceptType END
RETURN count(t);

// ---------------------------------------------------------------------------------------
// 4. 关系
// ---------------------------------------------------------------------------------------

// REQUIRES  菜谱 -> 食材（多对多，2892 条）
LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
WITH row WHERE row.relationshipType = '801000001'
MATCH (source:Recipe {nodeId: row.startNodeId})
MATCH (target:Ingredient {nodeId: row.endNodeId})
MERGE (source)-[r:REQUIRES]->(target)
SET r.relationshipId = row.relationshipId,
    r.amount         = CASE WHEN row.amount IN ['', 'null'] THEN null ELSE row.amount END,
    r.unit           = CASE WHEN row.unit IN ['', 'null'] THEN null ELSE row.unit END,
    r.isMain         = (row.isMain = 'True'),
    r.originalType   = row.relationshipType
RETURN count(r);

// CONTAINS_STEP  菜谱 -> 步骤（2507 条）
LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
WITH row WHERE row.relationshipType = '801000003'
MATCH (source:Recipe {nodeId: row.startNodeId})
MATCH (target:CookingStep {nodeId: row.endNodeId})
MERGE (source)-[r:CONTAINS_STEP]->(target)
SET r.relationshipId = row.relationshipId,
    r.stepOrder      = CASE WHEN row.step_order IN ['', 'null'] THEN null ELSE toFloat(row.step_order) END,
    r.originalType   = row.relationshipType
RETURN count(r);

// BELONGS_TO  菜谱 -> 分类（327 条，支持一菜多分类）
LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
WITH row WHERE row.relationshipType = '801000004'
MATCH (source:Recipe {nodeId: row.startNodeId})
MATCH (target:RecipeCategory {nodeId: row.endNodeId})
MERGE (source)-[r:BELONGS_TO]->(target)
SET r.relationshipId = row.relationshipId,
    r.originalType   = row.relationshipType
RETURN count(r);

// DIFFICULTY_LEVEL  菜谱 -> 难度（321 条）
LOAD CSV WITH HEADERS FROM 'file:///relationships.csv' AS row
WITH row WHERE row.relationshipType = '801000005'
MATCH (source:Recipe {nodeId: row.startNodeId})
MATCH (target:DifficultyLevel {nodeId: row.endNodeId})
MERGE (source)-[r:DIFFICULTY_LEVEL]->(target)
SET r.relationshipId = row.relationshipId,
    r.originalType   = row.relationshipType
RETURN count(r);

// ---------------------------------------------------------------------------------------
// 5. 自检：期望 3595 节点 / 6047 关系 / 255 个食材被 ≥2 道菜共享
// ---------------------------------------------------------------------------------------
MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n ORDER BY n DESC;

MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n ORDER BY n DESC;

MATCH (:Recipe)-[:REQUIRES]->(i:Ingredient)
WITH i, count(*) AS recipes
WHERE recipes >= 2
RETURN count(i) AS sharedIngredients, max(recipes) AS maxRecipes;

// 连通性分层：全部食材 2892 条边，其中主料 650 条。
// 只看主料时平均 1 跳可达 12.5 道菜、90 道菜孤立——说明连通主要由调料撑起，
// Day 4 的实体链接和 Day 7 的 benchmark 要按 isMain 分组。
MATCH ()-[r:REQUIRES]->() RETURN r.isMain AS isMain, count(*) AS n ORDER BY isMain DESC;

