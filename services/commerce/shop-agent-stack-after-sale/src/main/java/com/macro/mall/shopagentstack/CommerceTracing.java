package com.macro.mall.shopagentstack;

import io.opentelemetry.api.OpenTelemetry;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.trace.*;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.Scope;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import java.util.Set;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/** Opt-in diagnostics, never an authorization or refund source of truth. No global SDK. */
public final class CommerceTracing implements AutoCloseable {
    private static final Logger LOG=LoggerFactory.getLogger(CommerceTracing.class);
    private static final Pattern PARENT=Pattern.compile("00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}");
    private static final Set<String> NAMES=Set.of("commerce.request","refund.publish","refund.consume","db.refund.transaction");
    private final Tracer tracer;
    private final SdkTracerProvider provider;
    public final boolean trustInternal;
    public CommerceTracing(SdkTracerProvider provider,boolean trustInternal) {
        this.provider=provider;this.trustInternal=trustInternal;
        this.tracer=provider==null?OpenTelemetry.noop().getTracer("shop-commerce"):provider.get("shop-commerce","m1.3b");
    }
    public static CommerceTracing disabled(){return new CommerceTracing(null,false);}
    public boolean enabled(){return provider!=null;}
    /** Restrict to bounded W3C v00, drop tracestate/baggage, never preserve arbitrary flags. */
    public static Context parent(Object raw) {
        if(!(raw instanceof String value) || !PARENT.matcher(value).matches()) return Context.root();
        String[] parts=value.split("-");
        if(parts[1].equals("0".repeat(32))||parts[2].equals("0".repeat(16))) return Context.root();
        var flags=(Integer.parseInt(parts[3],16)&1)==1?TraceFlags.getSampled():TraceFlags.getDefault();
        return Context.root().with(Span.wrap(SpanContext.createFromRemoteParent(parts[1],parts[2],flags,TraceState.getDefault())));
    }
    public String currentParent() {
        if(!enabled()) return null;
        var context=Span.current().getSpanContext();
        if(!context.isValid()) return null;
        return "00-"+context.getTraceId()+"-"+context.getSpanId()+"-"+(context.isSampled()?"01":"00");
    }
    public Operation start(String name,SpanKind kind,Context parent) {
        if(!NAMES.contains(name)) throw new IllegalArgumentException("Unregistered diagnostic operation");
        Span span=tracer.spanBuilder(name).setSpanKind(kind).setParent(parent).startSpan();
        return new Operation(span);
    }
    /** Best-effort sidecar in the SAME approval transaction; failure never changes business intent. */
    public void captureIntent(JdbcTemplate db,long id) {
        String value=currentParent();
        if(value==null) return;
        try {db.update("INSERT INTO shop_agent_stack_refund_trace(case_id,traceparent) VALUES(?,?)",id,value);}
        catch(DataAccessException e){LOG.warn("shop_trace_intent_unavailable");}
    }
    public Context intentParent(JdbcTemplate db,long id) {
        if(!enabled()) return Context.root();
        try {
            var rows=db.queryForList("SELECT traceparent FROM shop_agent_stack_refund_trace WHERE case_id=?",id);
            return rows.size()==1?parent(rows.get(0).get("traceparent")):Context.root();
        } catch(DataAccessException e){LOG.warn("shop_trace_intent_unavailable");return Context.root();}
    }
    @Override public void close(){if(provider!=null) provider.shutdown().join(3,TimeUnit.SECONDS);}
    public static final class Operation implements AutoCloseable {
        public final Span span;
        private final Scope scope;
        private Operation(Span span){this.span=span;this.scope=span.makeCurrent();}
        public void failed(){span.setStatus(StatusCode.ERROR);}
        @Override public void close(){try{scope.close();}finally{span.end();}}
    }
}
