package com.macro.mall.shopagentstack;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;
import org.springframework.transaction.annotation.EnableTransactionManagement;
import javax.sql.DataSource;
import java.math.BigDecimal;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@SpringJUnitConfig(DemoImportTest.Config.class)
class DemoImportTest {
    @Configuration @EnableTransactionManagement static class Config {
        @Bean DataSource dataSource() { return new DriverManagerDataSource("jdbc:h2:mem:demoimport;MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_DELAY=-1", "sa", ""); }
        @Bean JdbcTemplate db(DataSource ds) { return new JdbcTemplate(ds); }
        @Bean DataSourceTransactionManager transactionManager(DataSource ds) { return new DataSourceTransactionManager(ds); }
        static PolicyService policy;
        @Bean DemoImportService importer(JdbcTemplate db) { policy=spy(new PolicyService(db)); return new DemoImportService(db,policy,new ObjectMapper()); }
    }
    @Autowired JdbcTemplate db;
    @Autowired DemoImportService importer;
    PolicyService policies;
    @BeforeEach void setup() {
        policies=Config.policy; reset(policies);
        db.execute("DROP ALL OBJECTS");
        db.execute("CREATE TABLE shop_agent_stack_demo_seed(id INT PRIMARY KEY,bundle_hash VARCHAR(64),receipt VARCHAR(2000000))");
        db.update("INSERT INTO shop_agent_stack_demo_seed(id) VALUES(1)");
        db.execute("CREATE TABLE shop_agent_stack_demo_import(id VARCHAR(36) PRIMARY KEY,actor_id BIGINT,action VARCHAR(16),bundle_hash VARCHAR(64),confirmation_hash VARCHAR(64),expected_state VARCHAR(2000000),payload VARCHAR(2000000),status VARCHAR(16) DEFAULT 'PREVIEW',expires_at TIMESTAMP,result VARCHAR(2000000),applied_at TIMESTAMP)");
        db.execute("CREATE TABLE pms_brand(id BIGINT PRIMARY KEY,name VARCHAR(120),sort INT,show_status INT,product_count INT,product_comment_count INT,brand_story VARCHAR(200))");
        db.execute("CREATE TABLE pms_product_category(id BIGINT PRIMARY KEY,parent_id BIGINT,name VARCHAR(120),level INT,product_count INT,product_unit VARCHAR(20),nav_status INT,show_status INT,sort INT,description VARCHAR(200))");
        db.execute("CREATE TABLE pms_product(id BIGINT PRIMARY KEY,brand_id BIGINT,product_category_id BIGINT,product_attribute_category_id BIGINT,name VARCHAR(100),pic VARCHAR(200),product_sn VARCHAR(200) UNIQUE,delete_status INT,publish_status INT,new_status INT,recommand_status INT,verify_status INT,sort INT,sale INT,price DECIMAL(10,2),original_price DECIMAL(10,2),stock INT,low_stock INT,unit VARCHAR(20),weight INT,preview_status INT,promotion_type INT,gift_growth INT,gift_point INT,brand_name VARCHAR(100),product_category_name VARCHAR(100),sub_title VARCHAR(200),description VARCHAR(1000),detail_title VARCHAR(100),detail_desc VARCHAR(3000),note VARCHAR(100),keywords VARCHAR(200))");
        db.execute("CREATE TABLE pms_sku_stock(id BIGINT PRIMARY KEY,product_id BIGINT,sku_code VARCHAR(200) UNIQUE,price DECIMAL(10,2),stock INT,low_stock INT,sale INT,lock_stock INT,sp_data VARCHAR(2000),pic VARCHAR(200))");
        db.execute("CREATE TABLE shop_agent_stack_policy(id BIGINT AUTO_INCREMENT PRIMARY KEY,title VARCHAR(120),content VARCHAR(20000),author_id BIGINT,version INT,status VARCHAR(20) DEFAULT 'DRAFT')");
        db.execute("CREATE TABLE shop_agent_stack_policy_meta(policy_id BIGINT PRIMARY KEY,family_id BIGINT,visibility VARCHAR(20))");
    }
    DemoImportService.Bundle bundle() {
        var product=new DemoImportService.Product("demo-cup","杯具","演示杯",new BigDecimal("49.90"),10,200,"陶瓷","一只","合成示例","手洗");
        return new DemoImportService.Bundle("a".repeat(64),List.of(product),List.of(new DemoImportService.Policy("POL-001","Demo policy","First clause\nSecond clause","CUSTOMER")));
    }
    Map<String,Object> preview(String action) { return importer.preview(bundle(),action,9); }
    Map<String,Object> apply(Map<String,Object> p) { return importer.apply(p.get("id").toString(),p.get("confirmation_hash").toString(),9); }
    int count(String table) { return db.queryForObject("SELECT COUNT(*) FROM "+table,Integer.class); }
    void seed() { apply(preview("SEED")); }

    @Test void previewWritesOnlyTheAuditPlan() {
        var p=preview("SEED"); assertEquals("PREVIEW",p.get("status")); assertEquals(1,count("shop_agent_stack_demo_import"));
        for(String table:List.of("pms_brand","pms_product","pms_sku_stock","shop_agent_stack_policy")) assertEquals(0,count(table));
    }
    @Test void applyCreatesProductsAndDraftsButDoesNotPublish() {
        var r=apply(preview("SEED")); assertEquals(1,r.get("productsCreated")); assertEquals(1,r.get("draftsCreated"));
        assertEquals("DRAFT",db.queryForObject("SELECT status FROM shop_agent_stack_policy",String.class));
        assertEquals(1,count("pms_sku_stock")); verify(policies,never()).publish(anyLong());
    }
    @Test void replayReturnsReceiptWithoutResettingStock() {
        var p=preview("SEED"); var r=apply(p); db.update("UPDATE pms_sku_stock SET stock=7,sale=3,lock_stock=2");
        assertEquals(r,apply(p)); assertEquals(7,db.queryForObject("SELECT stock FROM pms_sku_stock",Integer.class));
        assertEquals(1,count("shop_agent_stack_policy"));
    }
    @Test void freshPreviewOfSeededBundleIsNoop() {
        seed(); var p=preview("SEED"); var r=apply(p); assertEquals(0,r.get("productsCreated")); assertEquals(0,r.get("draftsCreated"));
    }
    @Test void wrongConfirmationWritesNothing() {
        var p=preview("SEED"); assertThrows(RuntimeException.class,()->importer.apply(p.get("id").toString(),"0".repeat(64),9));
        assertEquals(0,count("pms_product"));
    }
    @Test void anotherAdministratorCannotReadOrConsumePreview() {
        var p=preview("SEED"); String id=p.get("id").toString();
        assertThrows(RuntimeException.class,()->importer.view(id,10));
        assertThrows(RuntimeException.class,()->importer.apply(id,p.get("confirmation_hash").toString(),10));
    }
    @Test void expiredPreviewDoesNotWrite() {
        var p=preview("SEED"); db.update("UPDATE shop_agent_stack_demo_import SET expires_at=TIMESTAMP '2000-01-01 00:00:00'");
        assertThrows(RuntimeException.class,()->apply(p)); assertEquals(0,count("pms_product"));
    }
    @Test void collisionAfterPreviewRollsBackWholeImport() {
        var p=preview("SEED"); db.update("INSERT INTO pms_sku_stock(id,product_id) VALUES(10001,50)");
        assertThrows(RuntimeException.class,()->apply(p)); assertEquals(0,count("pms_brand"));
        assertEquals("PREVIEW",importer.view(p.get("id").toString(),9).get("status"));
    }
    @Test void policyFailureRollsBackProductsAndReceipt() {
        var p=preview("SEED"); doThrow(new IllegalStateException("injected downstream failure")).when(policies).draft(anyString(),anyString(),anyLong(),anyString(),isNull());
        assertThrows(RuntimeException.class,()->apply(p)); assertEquals(0,count("pms_product")); assertEquals(0,count("pms_brand"));
        assertNull(db.queryForObject("SELECT bundle_hash FROM shop_agent_stack_demo_seed",String.class));
    }
    @Test void publicationRequiresExistingSeed() { assertThrows(RuntimeException.class,()->preview("PUBLISH")); }
    @Test void publishingIsSeparateAndReplaySafe() {
        seed(); var p=preview("PUBLISH");
        assertEquals("DRAFT",db.queryForObject("SELECT status FROM shop_agent_stack_policy",String.class));
        // This unit isolates orchestration. MySQL/E2E exercises the real PolicyService publisher.
        doAnswer(c->{db.update("UPDATE shop_agent_stack_policy SET status='PUBLISHED' WHERE id=?",c.getArgument(0,Long.class));return null;}).when(policies).publish(anyLong());
        var r=apply(p); assertEquals(1,r.get("policiesPublished")); assertEquals(r,apply(p)); verify(policies,times(1)).publish(anyLong());
    }
    @Test void changedPolicyRejectsStalePublication() {
        seed(); var p=preview("PUBLISH"); db.update("UPDATE shop_agent_stack_policy SET content='changed by another actor'");
        assertThrows(RuntimeException.class,()->apply(p)); verify(policies,never()).publish(anyLong());
    }
    @Test void withdrawnPolicyCannotBeRevivedByImporter() {
        seed(); db.update("UPDATE shop_agent_stack_policy SET status='WITHDRAWN'");
        assertThrows(RuntimeException.class,()->preview("PUBLISH"));
    }
    @Test void differentBundleCannotUpgradeExistingData() {
        seed(); var b=bundle(); var changed=new DemoImportService.Bundle("b".repeat(64),b.products(),b.policies());
        assertThrows(RuntimeException.class,()->importer.preview(changed,"SEED",9));
    }
    @Test void concurrentApplyConsumesOnce() throws Exception {
        var p=preview("SEED"); ExecutorService pool=Executors.newFixedThreadPool(2);
        try { var results=pool.invokeAll(List.of(()->apply(p),()->apply(p))); assertEquals(results.get(0).get(),results.get(1).get()); }
        finally { pool.shutdownNow(); }
        assertEquals(1,count("pms_product")); assertEquals(1,count("shop_agent_stack_policy"));
    }
    @Test void secondOutstandingPlanBecomesStale() {
        var p=preview("SEED"); var other=preview("SEED"); apply(p);
        assertThrows(RuntimeException.class,()->apply(other)); assertEquals(1,count("pms_product"));
    }
    @Test void invalidSourceAndVisibilityRejectedBeforePlan() {
        var b=bundle(); assertThrows(RuntimeException.class,()->importer.preview(new DemoImportService.Bundle("invalid",b.products(),b.policies()),"SEED",9));
        var hidden=new DemoImportService.Policy("SOP-001","Hidden","staff only","CUSTOMER");
        assertThrows(RuntimeException.class,()->importer.preview(new DemoImportService.Bundle(b.sourceSha256(),b.products(),List.of(hidden)),"SEED",9));
        assertEquals(0,count("shop_agent_stack_demo_import"));
    }
    @Test void databaseExpiryDoesNotCastMysqlDatetime() {
        JdbcTemplate connection=mock(JdbcTemplate.class);
        when(connection.queryForMap(anyString())).thenReturn(Map.of("id",1));
        String id="11111111-1111-1111-1111-111111111111", confirmation="c".repeat(64);
        when(connection.queryForList(anyString(),eq(id),eq(9L))).thenReturn(List.of(Map.of(
                "status","PREVIEW","confirmation_hash",confirmation,"unexpired",0,
                "expires_at",java.time.LocalDateTime.of(2099,1,1,0,0))));
        DemoImportService direct=new DemoImportService(connection,policies,new ObjectMapper());
        assertThrows(com.macro.mall.common.exception.ApiException.class,()->direct.apply(id,confirmation,9));
        verify(connection,never()).update(anyString(),any(Object[].class));
    }
    @Test void databaseCreatesTenMinuteExpiry() {
        var p=preview("SEED");
        Long seconds=db.queryForObject("SELECT TIMESTAMPDIFF(SECOND,CURRENT_TIMESTAMP,expires_at) FROM shop_agent_stack_demo_import WHERE id=?",Long.class,p.get("id"));
        assertTrue(seconds>=590 && seconds<=600);
    }
    @Test void excessiveAndDuplicateInputsRejected() {
        var b=bundle(); assertThrows(RuntimeException.class,()->DemoImportService.validate(new DemoImportService.Bundle(b.sourceSha256(),Collections.nCopies(101,b.products().get(0)),b.policies())));
        assertThrows(RuntimeException.class,()->DemoImportService.validate(new DemoImportService.Bundle(b.sourceSha256(),b.products(),Collections.nCopies(2,b.policies().get(0)))));
    }
}
