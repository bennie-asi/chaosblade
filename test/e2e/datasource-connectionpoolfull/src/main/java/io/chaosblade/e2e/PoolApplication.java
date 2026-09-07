/*
 * Copyright 2025 The ChaosBlade Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package io.chaosblade.e2e;

import com.alibaba.druid.pool.DruidDataSource;
import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
public class PoolApplication {
  public static void main(String[] args) {
    SpringApplication.run(PoolApplication.class, args);
  }

  @Bean(name = "coreDataSource", destroyMethod = "close")
  DataSource coreDataSource(@Value("${pool.type:hikari}") String poolType) {
    if ("druid".equalsIgnoreCase(poolType)) {
      DruidDataSource dataSource = new DruidDataSource();
      dataSource.setName("coreDataSource");
      dataSource.setUrl("jdbc:h2:mem:druid;DB_CLOSE_DELAY=-1");
      dataSource.setUsername("sa");
      dataSource.setPassword("");
      dataSource.setDriverClassName("org.h2.Driver");
      dataSource.setInitialSize(1);
      dataSource.setMinIdle(1);
      dataSource.setMaxActive(4);
      dataSource.setMaxWait(1000);
      return dataSource;
    }
    HikariConfig config = new HikariConfig();
    config.setPoolName("coreDataSource");
    config.setJdbcUrl("jdbc:h2:mem:hikari;DB_CLOSE_DELAY=-1");
    config.setUsername("sa");
    config.setPassword("");
    config.setDriverClassName("org.h2.Driver");
    config.setMinimumIdle(1);
    config.setMaximumPoolSize(4);
    config.setConnectionTimeout(1000);
    return new HikariDataSource(config);
  }

  @Bean
  JdbcTemplate jdbcTemplate(DataSource coreDataSource) {
    return new JdbcTemplate(coreDataSource);
  }

  @RestController
  static class ProbeController {
    private final JdbcTemplate jdbcTemplate;
    private final DataSource dataSource;

    ProbeController(JdbcTemplate jdbcTemplate, DataSource coreDataSource) {
      this.jdbcTemplate = jdbcTemplate;
      this.dataSource = coreDataSource;
    }

    @GetMapping("/query")
    ResponseEntity<Map<String, Object>> query() {
      long startedAt = System.currentTimeMillis();
      Map<String, Object> body = new LinkedHashMap<String, Object>();
      try {
        Integer count = jdbcTemplate.queryForObject(
            "select count(*) from INFORMATION_SCHEMA.TABLES", Integer.class);
        body.put("ok", true);
        body.put("tables", count);
        body.put("elapsedMs", System.currentTimeMillis() - startedAt);
        return ResponseEntity.ok(body);
      } catch (RuntimeException failure) {
        body.put("ok", false);
        body.put("error", failure.getClass().getSimpleName());
        body.put("message", failure.getMessage());
        body.put("elapsedMs", System.currentTimeMillis() - startedAt);
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(body);
      }
    }

    @GetMapping("/pool")
    Map<String, Object> pool() {
      Map<String, Object> body = new LinkedHashMap<String, Object>();
      if (dataSource instanceof HikariDataSource) {
        HikariDataSource hikari = (HikariDataSource) dataSource;
        body.put("type", "HikariCP");
        body.put("maximum", hikari.getMaximumPoolSize());
        body.put("active", hikari.getHikariPoolMXBean().getActiveConnections());
        body.put("idle", hikari.getHikariPoolMXBean().getIdleConnections());
      } else {
        DruidDataSource druid = (DruidDataSource) dataSource;
        body.put("type", "Druid");
        body.put("maximum", druid.getMaxActive());
        body.put("active", druid.getActiveCount());
        body.put("idle", druid.getPoolingCount());
      }
      return body;
    }
  }
}
