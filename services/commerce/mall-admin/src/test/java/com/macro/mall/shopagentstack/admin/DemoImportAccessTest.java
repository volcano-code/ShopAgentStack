package com.macro.mall.shopagentstack.admin;

import com.macro.mall.shopagentstack.DemoImportService;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import java.security.Principal;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class DemoImportAccessTest {
    private final DemoImportService service=mock(DemoImportService.class);
    private final ShopAgentStackStaffAccess access=mock(ShopAgentStackStaffAccess.class);
    private final ShopAgentStackDemoImportController controller=new ShopAgentStackDemoImportController(service,access);
    @Test void endpointAbsentByDefault() {
        new ApplicationContextRunner().withBean(DemoImportService.class,()->service).withBean(ShopAgentStackStaffAccess.class,()->access)
            .withUserConfiguration(ShopAgentStackDemoImportController.class).run(c->assertTrue(c.getBeansOfType(ShopAgentStackDemoImportController.class).isEmpty()));
    }
    @Test void endpointExplicitlyEnabled() {
        new ApplicationContextRunner().withBean(DemoImportService.class,()->service).withBean(ShopAgentStackStaffAccess.class,()->access)
            .withPropertyValues("SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED=true").withUserConfiguration(ShopAgentStackDemoImportController.class)
            .run(c->assertEquals(1,c.getBeansOfType(ShopAgentStackDemoImportController.class).size()));
    }
    @Test void unauthenticatedPreviewHasNoServiceSideEffects() {
        when(access.require(null,true)).thenThrow(new IllegalStateException("denied"));
        assertThrows(RuntimeException.class,()->controller.preview(null,null,"SEED")); verifyNoInteractions(service);
    }
    @Test void currentRoleCheckedAgainAtApply() {
        Principal principal=()->"synthetic-staff"; when(access.require(principal,true)).thenThrow(new IllegalStateException("role revoked"));
        assertThrows(RuntimeException.class,()->controller.apply(principal,"id",new DemoImportService.Confirmation("confirmation")));
        verifyNoInteractions(service);
    }
    @Test void actorComesFromBackendNotRequestBody() {
        Principal principal=()->"synthetic-admin"; when(access.require(principal,true)).thenReturn(Map.of("id",9L));
        controller.view(principal,"preview-id"); verify(service).view("preview-id",9L);
    }
}
