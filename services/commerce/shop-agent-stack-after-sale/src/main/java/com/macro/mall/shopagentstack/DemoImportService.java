package com.macro.mall.shopagentstack;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.macro.mall.common.exception.Asserts;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Opt-in local demo only. Java owns every mutation; preview never publishes policies. */
@Service
@ConditionalOnProperty(name="SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED", havingValue="true")
public class DemoImportService {
    static final String MARKER = "shop_agent_stack-catalog-v1-original-synthetic";
    private final JdbcTemplate db;
    private final PolicyService policies;
    private final ObjectMapper json;
    public DemoImportService(JdbcTemplate db, PolicyService policies, ObjectMapper json) {
        this.db = db; this.policies = policies; this.json = json;
    }
    public record Product(String slug, String category, String name, BigDecimal price, Integer stock,
                          Integer weightGrams, String material, String specification, String description, String care) {}
    public record Policy(String sourceId, String title, String content, String visibility) {}
    public record Bundle(String sourceSha256, List<Product> products, List<Policy> policies) {}
    public record Confirmation(String confirmation) {}

    private static void require(boolean condition, String message) { if (!condition) Asserts.fail(message); }
    private static void text(String s, int max) {
        require(s != null && !s.isBlank() && s.length() <= max && s.equals(s.trim()) && s.indexOf('\0') < 0,
                "Invalid demo field");
    }
    private String encode(Object value) {
        try { return json.writeValueAsString(value); }
        catch (Exception e) { throw new IllegalStateException("Demo serialization failed"); }
    }
    private <T> T decode(String value, Class<T> type) {
        try { return json.readValue(value, type); }
        catch (Exception e) { throw new IllegalStateException("Stored demo plan invalid"); }
    }
    private Map<String,Object> map(String value) {
        try { return json.readValue(value, new TypeReference<Map<String,Object>>() {}); }
        catch (Exception e) { throw new IllegalStateException("Stored demo receipt invalid"); }
    }
    private static String digest(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException("SHA-256 unavailable"); }
    }
    static void validate(Bundle b) {
        require(b != null && b.sourceSha256() != null && b.sourceSha256().matches("[0-9a-f]{64}"), "Invalid source digest");
        require(b.products() != null && !b.products().isEmpty() && b.products().size() <= 100 &&
                b.policies() != null && !b.policies().isEmpty() && b.policies().size() <= 96, "Invalid demo bundle size");
        Set<String> slugs = new HashSet<>(), names = new HashSet<>(), categories = new HashSet<>(), ids = new HashSet<>(), titles = new HashSet<>();
        for (Product p : b.products()) {
            require(p != null, "Null demo product"); text(p.slug(), 80);
            require(p.slug().matches("[a-z]+(?:-[a-z]+)*") && slugs.add(p.slug()) && names.add(p.name()), "Duplicate/invalid demo product");
            text(p.name(), 64); text(p.category(), 64); text(p.material(), 120); text(p.specification(), 200);
            text(p.description(), 1000); text(p.care(), 500); categories.add(p.category());
            require(p.price() != null && p.price().scale() <= 2 && p.price().signum() > 0 && p.price().compareTo(new BigDecimal("1000000")) <= 0,
                    "Invalid demo price");
            require(p.stock() != null && p.stock() > 0 && p.stock() <= 1000000 && p.weightGrams() != null &&
                    p.weightGrams() > 0 && p.weightGrams() <= 1000000, "Invalid demo inventory");
        }
        require(categories.size() <= 10, "Too many demo categories");
        for (Policy p : b.policies()) {
            require(p != null, "Null demo policy"); text(p.sourceId(), 20); text(p.title(), 120); text(p.content(), 20000);
            require(p.sourceId().matches("(?:POL|SOP)-[0-9]{3}") && ids.add(p.sourceId()) && titles.add(p.title()), "Duplicate/invalid demo policy");
            require((p.sourceId().startsWith("POL-") ? "CUSTOMER" : "STAFF").equals(p.visibility()), "Demo visibility mismatch");
        }
    }
    private Map<String,Object> slot() { return db.queryForMap("SELECT * FROM shop_agent_stack_demo_seed WHERE id=1 FOR UPDATE"); }
    private void absent(String sql, Object... args) {
        require(db.queryForList(sql + " FOR UPDATE", args).isEmpty(), "Existing demo catalog/policy conflict; nothing overwritten");
    }
    private List<String> categories(Bundle b) { return b.products().stream().map(Product::category).distinct().toList(); }
    private List<Long> policyIds(Map<String,Object> slot) {
        Object list = map(slot.get("receipt").toString()).get("policyIds");
        require(list instanceof List<?>, "Missing demo policy mapping");
        return ((List<?>)list).stream().map(x -> ((Number)x).longValue()).toList();
    }
    /** Returns the complete relevant precondition, not current stock or personal/order data. */
    private Map<String,Object> inspect(Bundle b, String hash, String action, Map<String,Object> slot) {
        Object saved = slot.get("bundle_hash");
        require(saved == null || hash.equals(saved), "Different bundle already installed; no automatic upgrades");
        if ("SEED".equals(action)) {
            if (saved != null) return Map.of("mode", "ALREADY_SEEDED");
            absent("SELECT id FROM pms_brand WHERE id=101");
            for (int i=0; i<categories(b).size(); i++) absent("SELECT id FROM pms_product_category WHERE id=?", 1011+i);
            for (int i=0; i<b.products().size(); i++) {
                Product p = b.products().get(i); long id = 10001+i;
                String sn = "SHOP_AGENT_STACK-LIFE-" + p.slug().toUpperCase(Locale.ROOT);
                absent("SELECT id FROM pms_product WHERE id=? OR product_sn=?", id, sn);
                absent("SELECT id FROM pms_sku_stock WHERE id=? OR product_id=? OR sku_code=?", id, id, sn+"-STD");
            }
            for (Policy p : b.policies()) absent("SELECT id FROM shop_agent_stack_policy WHERE title=?", p.title());
            return Map.of("mode", "CREATE_PRODUCTS_AND_DRAFTS");
        }
        require("PUBLISH".equals(action) && saved != null, "Seed drafts before requesting publication");
        List<Long> ids = policyIds(slot);
        require(ids.size() == b.policies().size(), "Demo policy mapping incomplete");
        List<String> statuses = new ArrayList<>();
        for (int i=0; i<ids.size(); i++) {
            var rows = db.queryForList("SELECT p.title,p.content,p.status,p.version,m.visibility,m.family_id FROM shop_agent_stack_policy p JOIN shop_agent_stack_policy_meta m ON m.policy_id=p.id WHERE p.id=? FOR UPDATE", ids.get(i));
            require(rows.size()==1, "Imported policy missing"); var row=rows.get(0); Policy p=b.policies().get(i);
            require(p.title().equals(row.get("title")) && p.content().equals(row.get("content")) && p.visibility().equals(row.get("visibility")) &&
                    ((Number)row.get("version")).intValue()==1 && ((Number)row.get("family_id")).longValue()==ids.get(i) &&
                    Set.of("DRAFT","PUBLISHED").contains(row.get("status")), "Imported policy changed/revised/withdrawn; review individually");
            statuses.add(row.get("status").toString());
        }
        return Map.of("mode", "PUBLISH_REVIEWED_POLICIES", "policyIds", ids, "statuses", statuses);
    }
    @Transactional
    public Map<String,Object> preview(Bundle b, String action, long actor) {
        require(actor>0 && Set.of("SEED","PUBLISH").contains(action), "Invalid demo action"); validate(b);
        String payload=encode(b), hash=digest(payload);
        require(payload.getBytes(StandardCharsets.UTF_8).length<=1024*1024, "Demo payload too large");
        var currentSlot=slot();
        // Bound outstanding plans per actor; nothing is auto-deleted on repeated preview.
        require(db.queryForObject("SELECT COUNT(*) FROM shop_agent_stack_demo_import WHERE actor_id=? AND status='PREVIEW' AND expires_at>CURRENT_TIMESTAMP", Integer.class, actor)<20,
                "Too many outstanding demo previews");
        var expected=inspect(b,hash,action,currentSlot); String id=UUID.randomUUID().toString();
        String confirmation=digest(id+"\n"+actor+"\n"+action+"\n"+hash+"\n"+encode(expected));
        db.update("INSERT INTO shop_agent_stack_demo_import(id,actor_id,action,bundle_hash,confirmation_hash,expected_state,payload,expires_at) VALUES(?,?,?,?,?,?,?,TIMESTAMPADD(SECOND,600,CURRENT_TIMESTAMP))",
                id,actor,action,hash,confirmation,encode(expected),payload);
        return view(id,actor);
    }
    @Transactional(readOnly=true)
    public Map<String,Object> view(String id,long actor) {
        var row=owned(id,actor,false);
        Map<String,Object> result=new LinkedHashMap<>();
        for (String key : List.of("id","action","bundle_hash","confirmation_hash","status","expires_at")) result.put(key,row.get(key));
        result.put("precondition",map(row.get("expected_state").toString()));
        result.put("review",decode(row.get("payload").toString(),Bundle.class));
        if (row.get("result")!=null) result.put("result",map(row.get("result").toString()));
        return result;
    }
    private Map<String,Object> owned(String id,long actor,boolean lock) {
        require(id!=null && id.matches("[0-9a-f-]{36}") && actor>0,"Invalid preview identity");
        var rows=db.queryForList("SELECT *,CASE WHEN expires_at>CURRENT_TIMESTAMP THEN 1 ELSE 0 END AS unexpired FROM shop_agent_stack_demo_import WHERE id=? AND actor_id=?"+(lock?" FOR UPDATE":""),id,actor);
        require(rows.size()==1,"Preview unavailable for this administrator"); return rows.get(0);
    }
    @Transactional
    public Map<String,Object> apply(String id, String confirmation, long actor) {
        var slot=slot(); // All importers serialize before locking a plan or business rows.
        var row=owned(id,actor,true);
        // DB clock is authoritative; MySQL DATETIME may map to LocalDateTime, not Timestamp.
        require(confirmation!=null && confirmation.equals(row.get("confirmation_hash")),"Exact preview confirmation required");
        if ("APPLIED".equals(row.get("status"))) return map(row.get("result").toString()); // Receipt only, no repeated writes.
        require("PREVIEW".equals(row.get("status")) && row.get("unexpired") instanceof Number fresh && fresh.intValue()==1,"Preview expired");
        Bundle b=decode(row.get("payload").toString(),Bundle.class); validate(b);
        String hash=row.get("bundle_hash").toString(), action=row.get("action").toString();
        require(hash.equals(digest(encode(b))),"Stored payload hash mismatch");
        Map<String,Object> expected=map(row.get("expected_state").toString());
        require(expected.equals(map(encode(inspect(b,hash,action,slot)))),"Preview stale; request a fresh preview");
        Map<String,Object> result=new LinkedHashMap<>();
        result.put("previewId",id); result.put("bundleHash",hash); result.put("action",action);
        int products=0,drafts=0,published=0;
        if ("SEED".equals(action) && slot.get("bundle_hash")==null) {
            insertProducts(b); products=b.products().size();
            List<Long> ids=new ArrayList<>();
            for (Policy p:b.policies()) ids.add(policies.draft(p.title(),p.content(),actor,p.visibility(),null));
            drafts=ids.size();
            db.update("UPDATE shop_agent_stack_demo_seed SET bundle_hash=?,receipt=? WHERE id=1",hash,encode(Map.of("policyIds",ids)));
            result.put("policyIds",ids);
        } else if ("PUBLISH".equals(action)) {
            List<Long> ids=policyIds(slot); List<?> statuses=(List<?>)expected.get("statuses");
            for (int i=0;i<ids.size();i++) if ("DRAFT".equals(statuses.get(i))) { policies.publish(ids.get(i)); published++; }
            result.put("policyIds",ids);
        }
        result.put("productsCreated",products); result.put("draftsCreated",drafts); result.put("policiesPublished",published);
        result.put("stockReset",false); result.put("status","APPLIED");
        db.update("UPDATE shop_agent_stack_demo_import SET status='APPLIED',result=?,applied_at=CURRENT_TIMESTAMP WHERE id=?",encode(result),id);
        return map(encode(result)); // Same JSON number types for first delivery and receipt replay.
    }
    private void insertProducts(Bundle b) {
        List<String> cats=categories(b);
        db.update("INSERT INTO pms_brand(id,name,sort,show_status,product_count,product_comment_count,brand_story) VALUES(101,?,10,1,?,0,?)",
                "ShopAgentStack Studio",b.products().size(),"原创合成体验商品，非真实交易");
        for(int i=0;i<cats.size();i++) db.update("INSERT INTO pms_product_category(id,parent_id,name,level,product_count,product_unit,nav_status,show_status,sort,description) VALUES(?,0,?,0,?, ?,1,1,?,?)",
                1011+i,cats.get(i),Collections.frequency(b.products().stream().map(Product::category).toList(),cats.get(i)),"件",10-i,MARKER);
        for(int i=0;i<b.products().size();i++) {
            Product p=b.products().get(i); long id=10001+i;
            String sn="SHOP_AGENT_STACK-LIFE-"+p.slug().toUpperCase(Locale.ROOT),pic="/products/catalog-v1/"+p.slug()+".webp";
            db.update("INSERT INTO pms_product(id,brand_id,product_category_id,product_attribute_category_id,name,pic,product_sn,delete_status,publish_status,new_status,recommand_status,verify_status,sort,sale,price,original_price,stock,low_stock,unit,weight,preview_status,promotion_type,gift_growth,gift_point,brand_name,product_category_name,sub_title,description,detail_title,detail_desc,note,keywords) VALUES(?,101,?,0,?,?,?,0,1,0,1,1,?,0,?,?,?,10,?,?,0,0,0,0,?,?,?,?,?,?,?,?)",
                    id,1011+cats.indexOf(p.category()),p.name(),pic,sn,100-i,p.price(),p.price(),p.stock(),"件",p.weightGrams(),
                    "ShopAgentStack Studio",p.category(),p.specification(),p.description(),p.name(),
                    "材质："+p.material()+"\n规格："+p.specification()+"\n养护："+p.care()+"\n原创合成演示商品，AI示意图，无真实发货。",MARKER,p.category()+" "+p.material());
            db.update("INSERT INTO pms_sku_stock(id,product_id,sku_code,price,stock,low_stock,sale,lock_stock,sp_data,pic) VALUES(?,?,?,?,?,10,0,0,?,?)",
                    id,id,sn+"-STD",p.price(),p.stock(),encode(List.of(Map.of("key","规格","value",p.specification()),Map.of("key","材质","value",p.material()))),pic);
        }
    }
}
