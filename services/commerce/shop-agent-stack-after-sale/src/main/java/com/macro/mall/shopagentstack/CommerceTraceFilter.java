package com.macro.mall.shopagentstack;

import io.opentelemetry.api.trace.SpanKind;
import io.opentelemetry.context.Context;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import java.io.IOException;
import java.util.Collections;
import java.util.Set;

/** Synchronous servlet requests only. No request URL, arguments, principal or response bodies. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+10)
public class CommerceTraceFilter extends OncePerRequestFilter {
    private final CommerceTracing tracing;
    public CommerceTraceFilter(CommerceTracing tracing){this.tracing=tracing;}
    @Override protected boolean shouldNotFilter(HttpServletRequest r){return !tracing.enabled()||r.getRequestURI().startsWith("/actuator/");}
    @Override protected void doFilterInternal(HttpServletRequest req,HttpServletResponse res,FilterChain chain) throws ServletException,IOException {
        Context parent=Context.root();
        // Public requests always start new traces, even when internal propagation is enabled.
        if(tracing.trustInternal&&req.getRequestURI().startsWith("/shop_agent_stack/internal/agent/")) {
            var headers=Collections.list(req.getHeaders("traceparent"));
            if(headers.size()==1) parent=CommerceTracing.parent(headers.get(0));
        }
        try(var operation=tracing.start("commerce.request",SpanKind.SERVER,parent)) {
            String method=req.getMethod();
            operation.span.setAttribute("http.request.method",Set.of("GET","POST","PUT","PATCH","DELETE","HEAD","OPTIONS").contains(method)?method:"_OTHER");
            res.setHeader("X-Trace-ID",operation.span.getSpanContext().getTraceId());
            try{chain.doFilter(req,res);}
            catch(IOException|ServletException|RuntimeException e){operation.failed();throw e;}
            finally{
                operation.span.setAttribute("http.response.status_code",res.getStatus());
                if(res.getStatus()>=500) operation.failed();
            }
        }
    }
}
