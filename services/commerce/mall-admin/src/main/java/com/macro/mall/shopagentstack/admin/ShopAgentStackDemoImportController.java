package com.macro.mall.shopagentstack.admin;

import com.macro.mall.common.api.CommonResult;
import com.macro.mall.shopagentstack.DemoImportService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.*;
import java.security.Principal;

/** Not an Agent tool. Disabled outside an explicitly configured local demo. */
@RestController
@RequestMapping("/shop_agent_stack/demo-imports")
@ConditionalOnProperty(name="SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED", havingValue="true")
public class ShopAgentStackDemoImportController {
    private final DemoImportService imports;
    private final ShopAgentStackStaffAccess access;
    public ShopAgentStackDemoImportController(DemoImportService imports, ShopAgentStackStaffAccess access) {
        this.imports=imports; this.access=access;
    }
    private long admin(Principal principal) { return ((Number)access.require(principal,true).get("id")).longValue(); }
    @PostMapping("/preview") public CommonResult<?> preview(Principal p,@RequestBody DemoImportService.Bundle bundle,
            @RequestParam(defaultValue="SEED") String action) { return CommonResult.success(imports.preview(bundle,action,admin(p))); }
    @GetMapping("/{id}") public CommonResult<?> view(Principal p,@PathVariable String id) { return CommonResult.success(imports.view(id,admin(p))); }
    @PostMapping("/{id}/apply") public CommonResult<?> apply(Principal p,@PathVariable String id,
            @RequestBody DemoImportService.Confirmation confirmation) { return CommonResult.success(imports.apply(id,confirmation.confirmation(),admin(p))); }
}
