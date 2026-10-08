package com.macro.mall.shopagentstack;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;

/** Read-only bounded template loader; shares the exact domain bundle validation. */
public final class DemoImportTemplate {
    private DemoImportTemplate() {}
    public static DemoImportService.Bundle read(ObjectMapper json,String file) throws IOException {
        Path path=Path.of(file);
        if (!path.isAbsolute() || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) throw new IllegalArgumentException();
        byte[] bytes;
        try (var input=Files.newInputStream(path, LinkOption.NOFOLLOW_LINKS)) { bytes=input.readNBytes(1024*1024+1); }
        if (bytes.length>1024*1024) throw new IllegalArgumentException();
        var bundle=json.readValue(bytes, DemoImportService.Bundle.class);
        DemoImportService.validate(bundle);
        return bundle;
    }
}
