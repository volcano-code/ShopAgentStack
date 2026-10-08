package com.macro.mall.shopagentstack.admin;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.macro.mall.common.api.CommonResult;
import com.macro.mall.shopagentstack.DemoImportTemplate;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import java.security.Principal;

/** Private synthetic input, not a public web asset or a business mutation. */
@RestController
@ConditionalOnProperty(name={"SHOP_AGENT_STACK_DEMO_IMPORT_ENABLED", "SHOP_AGENT_STACK_DEMO_IMPORT_UI_ENABLED"}, havingValue="true")
public class DemoImportTemplateController {
    private final ShopAgentStackStaffAccess access;
    private final ObjectMapper json;
    private final String file;
    public DemoImportTemplateController(ShopAgentStackStaffAccess access, ObjectMapper json,
            @Value("${SHOP_AGENT_STACK_DEMO_IMPORT_BUNDLE_FILE:}") String file) {
        this.access=access; this.json=json; this.file=file;
    }
    @GetMapping("/shop_agent_stack/demo-imports/template")
    public ResponseEntity<CommonResult<?>> template(Principal principal) {
        access.require(principal, true); // Re-query current role before reading any template bytes.
        try {
            var bundle=DemoImportTemplate.read(json,file);
            return ResponseEntity.ok().cacheControl(CacheControl.noStore())
                .header("Pragma", "no-cache").body(CommonResult.success(bundle));
        } catch (Exception error) {
            // Never expose filesystem paths, data or parsing exception contents.
            throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "Demo review data unavailable");
        }
    }
}
