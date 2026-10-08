package com.macro.mall.shopagentstack;

import io.opentelemetry.api.trace.*;
import io.opentelemetry.context.Context;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor;
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter;
import org.junit.jupiter.api.*;
import org.springframework.mock.web.*;
import org.springframework.mock.env.MockEnvironment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.amqp.core.*;
import org.springframework.amqp.AmqpRejectAndDontRequeueException;
import org.springframework.amqp.rabbit.connection.ConnectionFactory;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;

class CommerceTracingTest {
    static final String PARENT="00-11111111111111111111111111111111-2222222222222222-03";
    InMemorySpanExporter exporter;
    CommerceTracing tracing;
    @BeforeEach void setup(){
        exporter=InMemorySpanExporter.create();
        tracing=new CommerceTracing(SdkTracerProvider.builder().addSpanProcessor(SimpleSpanProcessor.create(exporter)).build(),true);
    }
    @AfterEach void close(){tracing.close();assertFalse(Span.current().getSpanContext().isValid());}
    @Test void validW3cContextKeepsOnlySamplingBit(){
        var c=Span.fromContext(CommerceTracing.parent(PARENT)).getSpanContext();
        assertTrue(c.isRemote());assertTrue(c.isSampled());assertEquals("11".repeat(16),c.getTraceId());
        assertTrue(c.getTraceState().isEmpty());
    }
    @Test void malformedContextStartsNewRoot(){
        for(Object value:new Object[]{false,0,PARENT+"\n",PARENT.replace("1","a").toUpperCase(),"ff"+PARENT.substring(2),
            "00-"+"0".repeat(32)+"-"+"2".repeat(16)+"-01",PARENT.replace("2222222222222222","0000000000000000")})
            assertFalse(Span.fromContext(CommerceTracing.parent(value)).getSpanContext().isValid());
        assertFalse(Span.fromContext(CommerceTracing.parent(null)).getSpanContext().isValid());
    }
    @Test void publicHeaderNeverControlsTrace() throws Exception{
        var request=new MockHttpServletRequest("POST","/shop_agent_stack/after-sales/7/decision");
        request.addHeader("traceparent",PARENT);request.addHeader("baggage","key=synthetic-secret");
        var response=new MockHttpServletResponse();
        new CommerceTraceFilter(tracing).doFilter(request,response,(a,b)->{((jakarta.servlet.http.HttpServletResponse)b).setStatus(403);});
        var span=exporter.getFinishedSpanItems().get(0);
        assertNotEquals("11".repeat(16),span.getTraceId());assertFalse(span.getParentSpanContext().isValid());
        assertEquals(span.getTraceId(),response.getHeader("X-Trace-ID"));
        assertFalse(span.toString().contains("synthetic-secret"));assertEquals(403,response.getStatus());
    }
    @Test void internalHeaderContinuesWithoutChangingAuthorization() throws Exception{
        var request=new MockHttpServletRequest("GET","/shop_agent_stack/internal/agent/orders");
        request.addHeader("traceparent",PARENT);
        var response=new MockHttpServletResponse();
        new CommerceTraceFilter(tracing).doFilter(request,response,(a,b)->{((jakarta.servlet.http.HttpServletResponse)b).setStatus(401);});
        assertEquals("11".repeat(16),response.getHeader("X-Trace-ID"));assertEquals(401,response.getStatus());
        assertEquals("22".repeat(8),exporter.getFinishedSpanItems().get(0).getParentSpanId());
    }
    @Test void duplicateInternalContextRejected() throws Exception{
        var r=new MockHttpServletRequest("GET","/shop_agent_stack/internal/agent/orders");
        r.addHeader("traceparent",PARENT);r.addHeader("traceparent",PARENT);
        new CommerceTraceFilter(tracing).doFilter(r,new MockHttpServletResponse(),(a,b)->{});
        assertFalse(exporter.getFinishedSpanItems().get(0).getParentSpanContext().isValid());
    }
    @Test void exceptionPreservedButMessageNotExported(){
        RuntimeException original=new IllegalStateException("synthetic-secret-body");
        var request=new MockHttpServletRequest("POST","/any/private/path");
        assertSame(original,assertThrows(RuntimeException.class,()->new CommerceTraceFilter(tracing).doFilter(request,new MockHttpServletResponse(),(a,b)->{throw original;})));
        var span=exporter.getFinishedSpanItems().get(0);
        assertEquals(StatusCode.ERROR,span.getStatus().getStatusCode());assertEquals("",span.getStatus().getDescription());
        assertTrue(span.getEvents().isEmpty());assertFalse(span.toString().contains("synthetic-secret-body"));
    }
    @Test void disabledNeedsNoEndpointAndDoesNotTouchBusiness() throws Exception{
        var disabled=new CommerceTracingConfig().commerceTracing(new MockEnvironment());
        assertFalse(disabled.enabled());assertNull(disabled.currentParent());
        var db=mock(JdbcTemplate.class);disabled.captureIntent(db,7);disabled.intentParent(db,7);verifyNoInteractions(db);
        var res=new MockHttpServletResponse();
        new CommerceTraceFilter(disabled).doFilter(new MockHttpServletRequest("GET","/private"),res,(a,b)->{((jakarta.servlet.http.HttpServletResponse)b).setStatus(403);});
        assertEquals(403,res.getStatus());assertNull(res.getHeader("X-Trace-ID"));
    }
    @Test void diagnosticSidecarSharesRollbackAndMissingTableDoesNotBreakBusiness(){
        var ds=new DriverManagerDataSource("jdbc:h2:mem:trace-test;MODE=MySQL;DB_CLOSE_DELAY=-1","sa","");
        var db=new JdbcTemplate(ds);db.execute("DROP ALL OBJECTS");
        db.execute("CREATE TABLE shop_agent_stack_refund_trace(case_id BIGINT PRIMARY KEY,traceparent VARCHAR(55))");
        var tx=new TransactionTemplate(new DataSourceTransactionManager(ds));
        try(var operation=tracing.start("commerce.request",SpanKind.SERVER,Context.root())){
            assertThrows(IllegalStateException.class,()->tx.execute(s->{tracing.captureIntent(db,7);throw new IllegalStateException("rollback");}));
            assertEquals(0,db.queryForObject("SELECT COUNT(*) FROM shop_agent_stack_refund_trace",Integer.class));
            tx.execute(s->{tracing.captureIntent(db,7);return null;});
            assertEquals(operation.span.getSpanContext().getTraceId(),Span.fromContext(tracing.intentParent(db,7)).getSpanContext().getTraceId());
            db.execute("DROP TABLE shop_agent_stack_refund_trace");
            assertDoesNotThrow(()->tracing.captureIntent(db,8));
            assertFalse(Span.fromContext(tracing.intentParent(db,8)).getSpanContext().isValid());
        }
    }
    @Test void concurrentRequestContextsDoNotLeak() throws Exception{
        var pool=Executors.newFixedThreadPool(2);
        try{
            var tasks=new ArrayList<Future<String>>();
            for(int i=0;i<8;i++) tasks.add(pool.submit(()->{
                assertFalse(Span.current().getSpanContext().isValid());
                String trace;
                try(var op=tracing.start("commerce.request",SpanKind.SERVER,Context.root())){trace=op.span.getSpanContext().getTraceId();Thread.yield();assertEquals(trace,Span.current().getSpanContext().getTraceId());}
                assertFalse(Span.current().getSpanContext().isValid());return trace;
            }));
            var ids=new HashSet<String>();for(var f:tasks) ids.add(f.get(5,TimeUnit.SECONDS));assertEquals(8,ids.size());
        }finally{pool.shutdownNow();}
    }
    @Test void consumerWrapsTransactionAndPreservesErrors(){
        var refunds=mock(RefundService.class);var worker=new RefundWorker(mock(JdbcTemplate.class),refunds,mock(ConnectionFactory.class));
        ReflectionTestUtils.setField(worker,"tracing",tracing);
        var properties=new MessageProperties();properties.setHeader("traceparent",PARENT);
        doThrow(new IllegalStateException("synthetic-secret")).when(refunds).complete(7L);
        assertThrows(AmqpRejectAndDontRequeueException.class,()->worker.consume(new Message("7".getBytes(StandardCharsets.UTF_8),properties)));
        verify(refunds).recordFailure(7,"BUSINESS_STATE_CONFLICT");
        var spans=exporter.getFinishedSpanItems();assertEquals(2,spans.size());
        assertEquals("db.refund.transaction",spans.get(0).getName());assertEquals(spans.get(1).getSpanId(),spans.get(0).getParentSpanId());
        for(var s:spans){assertEquals("11".repeat(16),s.getTraceId());assertEquals(StatusCode.ERROR,s.getStatus().getStatusCode());assertTrue(s.getEvents().isEmpty());}
    }
    @Test void publisherInjectsOnlyContextAndAckDoesNotCompleteRefund(){
        var db=mock(JdbcTemplate.class);var refunds=mock(RefundService.class);
        var worker=new RefundWorker(db,refunds,mock(ConnectionFactory.class));var rabbit=mock(RabbitTemplate.class);
        ReflectionTestUtils.setField(worker,"tracing",tracing);ReflectionTestUtils.setField(worker,"rabbit",rabbit);
        when(db.queryForList(startsWith("SELECT case_id,attempts"))).thenReturn(List.of(Map.of("case_id",7L,"attempts",0)));
        when(db.queryForList(startsWith("SELECT traceparent"),eq(7L))).thenReturn(List.of(Map.of("traceparent",PARENT)));
        when(db.update(startsWith("UPDATE shop_agent_stack_refund_job SET attempts"),eq(7L))).thenReturn(1);
        doAnswer(inv->{
            Message m=inv.getArgument(2);var h=m.getMessageProperties().getHeaders();assertEquals(Set.of("traceparent"),h.keySet());
            assertEquals("11".repeat(16),Span.fromContext(CommerceTracing.parent(h.get("traceparent"))).getSpanContext().getTraceId());
            CorrelationData c=inv.getArgument(3);c.getFuture().complete(new CorrelationData.Confirm(true,null));return null;
        }).when(rabbit).send(eq(""),eq(RefundWorker.QUEUE),any(Message.class),any(CorrelationData.class));
        worker.dispatch();verifyNoInteractions(refunds);
        var span=exporter.getFinishedSpanItems().get(0);assertEquals("refund.publish",span.getName());assertEquals("22".repeat(8),span.getParentSpanId());
    }
    @Test void unsafeExporterConfigurationRejected(){
        for(String value:List.of("http://x/v1/traces?token=a","http://user:secret@x/v1/traces","file:///v1/traces","http://x/v1/traces#f","http://x:0/v1/traces"))
            assertThrows(IllegalArgumentException.class,()->CommerceTracingConfig.validateEndpoint(value));
        assertThrows(IllegalArgumentException.class,()->CommerceTracingConfig.flag(new MockEnvironment().withProperty("key","maybe"),"key"));
        assertDoesNotThrow(()->CommerceTracingConfig.validateEndpoint("http://otel-collector:4318/v1/traces"));
    }
}
