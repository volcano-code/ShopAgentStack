package com.macro.mall.shopagentstack;

import io.opentelemetry.api.common.AttributeKey;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.exporter.otlp.http.trace.OtlpHttpSpanExporter;
import io.opentelemetry.sdk.resources.Resource;
import io.opentelemetry.sdk.trace.SdkTracerProvider;
import io.opentelemetry.sdk.trace.export.BatchSpanProcessor;
import io.opentelemetry.sdk.trace.samplers.Sampler;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import java.net.URI;
import java.time.Duration;
import java.util.Set;

@Configuration(proxyBeanMethods=false)
public class CommerceTracingConfig {
    @Bean(destroyMethod="close") public CommerceTracing commerceTracing(Environment env) {
        if(!flag(env,"SHOP_AGENT_STACK_OTEL_ENABLED")) return CommerceTracing.disabled();
        String service=env.getProperty("SHOP_AGENT_STACK_OTEL_SERVICE","shop-commerce");
        if(!Set.of("shop-commerce-portal","shop-commerce-admin","shop-commerce").contains(service))
            throw new IllegalArgumentException("Invalid commerce telemetry service");
        String endpoint=env.getRequiredProperty("SHOP_AGENT_STACK_OTEL_TRACES_ENDPOINT");
        validateEndpoint(endpoint);
        double ratio=Double.parseDouble(env.getProperty("SHOP_AGENT_STACK_OTEL_SAMPLE_RATIO","0.1"));
        if(!Double.isFinite(ratio)||ratio<0||ratio>1) throw new IllegalArgumentException("Invalid sample ratio");
        // This is operator configuration, not a model/user-controlled endpoint. No detectors or payload logs.
        var exporter=OtlpHttpSpanExporter.builder().setEndpoint(endpoint).setTimeout(Duration.ofSeconds(2)).build();
        var processor=BatchSpanProcessor.builder(exporter).setMaxQueueSize(512).setMaxExportBatchSize(64)
            .setScheduleDelay(Duration.ofSeconds(1)).setExporterTimeout(Duration.ofSeconds(3)).build();
        var provider=SdkTracerProvider.builder().setResource(Resource.create(Attributes.of(AttributeKey.stringKey("service.name"),service)))
            .setSampler(Sampler.parentBased(Sampler.traceIdRatioBased(ratio))).addSpanProcessor(processor).build();
        return new CommerceTracing(provider,flag(env,"SHOP_AGENT_STACK_OTEL_TRUST_INTERNAL"));
    }
    static boolean flag(Environment env,String key) {
        String value=env.getProperty(key,"false");
        if(!Set.of("true","false").contains(value)) throw new IllegalArgumentException("Invalid tracing boolean");
        return value.equals("true");
    }
    static void validateEndpoint(String value) {
        URI u;
        try{u=URI.create(value);}catch(IllegalArgumentException e){throw new IllegalArgumentException("Invalid OTLP endpoint");}
        if(!("http".equals(u.getScheme())||"https".equals(u.getScheme()))||u.getHost()==null||u.getRawUserInfo()!=null
            ||u.getRawQuery()!=null||u.getRawFragment()!=null||!"/v1/traces".equals(u.getRawPath())
            ||u.getPort()==0||u.getPort()>65535||value.chars().anyMatch(c->c<=32))
            throw new IllegalArgumentException("Invalid OTLP endpoint");
    }
}
