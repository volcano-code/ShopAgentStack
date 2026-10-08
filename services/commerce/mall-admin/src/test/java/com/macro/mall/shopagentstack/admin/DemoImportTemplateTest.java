package com.macro.mall.shopagentstack.admin;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.web.server.ResponseStatusException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.Principal;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class DemoImportTemplateTest {
    @TempDir Path dir;
    final ShopAgentStackStaffAccess access=mock(ShopAgentStackStaffAccess.class);
    final ObjectMapper json=new ObjectMapper();
    final Principal admin=()->"synthetic-admin";
    String valid() {
        return "{\"sourceSha256\":\""+"a".repeat(64)+"\",\"products\":[{\"slug\":\"demo-cup\",\"category\":\"Synthetic\",\"name\":\"Cup\",\"price\":12.00,\"stock\":8,\"weightGrams\":100,\"material\":\"glass\",\"specification\":\"one\",\"description\":\"synthetic\",\"care\":\"wash\"}],\"policies\":[{\"sourceId\":\"SOP-001\",\"title\":\"Staff-only\",\"content\":\"Synthetic staff text\",\"visibility\":\"STAFF\"}]}";
    }
    DemoImportTemplateController controller(Path file) { return new DemoImportTemplateController(access,json,file.toString()); }
    @Test void authenticatedTemplateIsNotCacheable() throws Exception {
        Path file=dir.resolve("bundle.json"); Files.writeString(file,valid());
        when(access.require(admin,true)).thenReturn(Map.of("id",1));
        var response=controller(file).template(admin);
        assertEquals("no-store",response.getHeaders().getCacheControl());
        assertEquals(200,response.getStatusCode().value()); verify(access).require(admin,true);
    }
    @Test void absentUnlessBothFlagsEnabled() {
        for (String[] flags : new String[][]{{},{"SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED=true"},{"SHOP_AGENT_STACK_DEMO_IMPORT_UI_ENABLED=true"}})
            context().withPropertyValues(flags).run(c->assertTrue(c.getBeansOfType(DemoImportTemplateController.class).isEmpty()));
        context().withPropertyValues("SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED=true","SHOP_AGENT_STACK_DEMO_IMPORT_UI_ENABLED=true")
            .run(c->assertEquals(1,c.getBeansOfType(DemoImportTemplateController.class).size()));
    }
    ApplicationContextRunner context() { return new ApplicationContextRunner().withBean(ShopAgentStackStaffAccess.class,()->access)
        .withBean(ObjectMapper.class,()->json).withUserConfiguration(DemoImportTemplateController.class); }
    @Test void deniedBeforeOpeningFile() {
        when(access.require(null,true)).thenThrow(new IllegalStateException("denied"));
        var ex=assertThrows(IllegalStateException.class,()->controller(dir.resolve("missing-private-file")).template(null));
        assertEquals("denied",ex.getMessage());
    }
    @Test void malformedFileCannotEchoContentsOrPath() throws Exception {
        Path file=dir.resolve("private-name.json"); Files.writeString(file,"private-payload");
        var ex=assertThrows(ResponseStatusException.class,()->controller(file).template(admin));
        assertEquals(503,ex.getStatusCode().value());assertFalse(ex.toString().contains("private-"));assertNull(ex.getCause());
    }
    @Test void rejectedForInvalidVisibility() throws Exception {
        Path file=dir.resolve("bundle.json"); Files.writeString(file,valid().replace("\"STAFF\"","\"CUSTOMER\""));
        assertThrows(ResponseStatusException.class,()->controller(file).template(admin));
    }
    @Test void oversizedOrSymlinkFilesRefused() throws Exception {
        Path file=dir.resolve("large.json"); Files.write(file,new byte[1024*1024+1]);
        assertThrows(ResponseStatusException.class,()->controller(file).template(admin));
        Path link=dir.resolve("link.json");Files.createSymbolicLink(link,file);
        assertThrows(ResponseStatusException.class,()->controller(link).template(admin));
    }
    @Test void roleRevocationIsCheckedOnEveryRequest() throws Exception {
        Path file=dir.resolve("bundle.json");Files.writeString(file,valid());
        when(access.require(admin,true)).thenReturn(Map.of("id",1)).thenThrow(new IllegalStateException("revoked"));
        controller(file).template(admin);assertThrows(IllegalStateException.class,()->controller(file).template(admin));
        verify(access,times(2)).require(admin,true);
    }
}
