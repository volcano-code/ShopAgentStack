package com.macro.mall.shopagentstack;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import com.macro.mall.common.exception.ApiException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AgentOperationTest {
    private final JdbcTemplate db=mock(JdbcTemplate.class);
    private final AfterSaleService sales=mock(AfterSaleService.class);
    private final AgentOperationService service=new AgentOperationService(db,sales);

    @Test void expiredConfirmedOperationCannotSubmit() {
        when(db.queryForList(anyString(),eq("operation"),eq(1L))).thenReturn(List.of(Map.of("status","CONFIRMED","expires_at",Timestamp.from(Instant.now().minusSeconds(1)))));
        assertThrows(ApiException.class,()->service.execute(1,"operation"));
        verifyNoInteractions(sales);
    }

    @Test void previewCannotSkipConfirmation() {
        when(db.queryForList(anyString(),eq("operation"),eq(1L))).thenReturn(List.of(Map.of("status","PREVIEW","expires_at",Timestamp.from(Instant.now().plusSeconds(60)))));
        assertThrows(ApiException.class,()->service.execute(1,"operation"));
        verifyNoInteractions(sales);
    }

    @Test void expiredOrUnknownGrantCannotResolveIdentity() {
        when(db.queryForList(anyString(),anyString())).thenReturn(List.of());
        assertThrows(ApiException.class,()->service.identify("expired-synthetic-token"));
        verifyNoInteractions(sales);
    }

    @Test void publicPolicyCheckpointUsesDatabaseEpochClockAndEarliestBoundary() {
        java.util.Map<String,Object> row=new java.util.HashMap<>();
        row.put("epoch", 97L);
        row.put("now_us", 1000000L);
        row.put("next_start_us", 2000000L);
        row.put("next_end_us", 1500000L);
        when(db.queryForMap(argThat(sql->
            sql.contains("shop_agent_stack_knowledge_epoch") &&
            sql.contains("m.visibility='CUSTOMER'") &&
            sql.contains("m.index_status='READY'") &&
            sql.contains("UNIX_TIMESTAMP(NOW(6))")))).thenReturn(row);
        PolicyService.CatalogCheckpoint result=new PolicyService(db).checkpoint();
        assertEquals(97L,result.epoch());
        assertEquals(1000000L,result.serverTimeUs());
        assertEquals(1500000L,result.nextTransitionUs());
        verify(db,times(1)).queryForMap(anyString());
    }

    @Test void publicPolicyCheckpointSupportsNoScheduledTransition() {
        java.util.Map<String,Object> row=new java.util.HashMap<>();
        row.put("epoch", 0L);
        row.put("now_us", 2000000L);
        row.put("next_start_us", null);
        row.put("next_end_us", null);
        when(db.queryForMap(anyString())).thenReturn(row);
        var result=new PolicyService(db).checkpoint();
        assertNull(result.nextTransitionUs());
    }

    @Test void stalePolicyBoundaryNeverGrantsCachedRead() {
        when(db.queryForMap(anyString())).thenReturn(Map.of(
            "epoch",17L,"now_us",2000000L,"next_start_us",1900000L));
        assertThrows(IllegalStateException.class,()->new PolicyService(db).checkpoint());
    }

    @Test void checkpointSerializationRetainsNullBoundaryUnderPortalGlobalObjectMapper() throws Exception {
        var mapper=new com.fasterxml.jackson.databind.ObjectMapper();
        mapper.setSerializationInclusion(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL);
        String json=mapper.writeValueAsString(new PolicyService.CatalogCheckpoint(97L,1234567L,null));
        assertTrue(json.contains("\"nextTransitionUs\":null"),
            "The Agent requires an explicit NULL boundary rather than an omitted field");
        assertTrue(json.contains("\"serverTimeUs\":1234567"));
    }
}
