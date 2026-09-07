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

import static org.junit.jupiter.api.Assertions.assertEquals;

import com.alibaba.druid.pool.DruidDataSource;
import com.zaxxer.hikari.HikariDataSource;
import javax.sql.DataSource;
import org.junit.jupiter.api.Test;
import org.springframework.boot.WebApplicationType;
import org.springframework.boot.builder.SpringApplicationBuilder;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.jdbc.core.JdbcTemplate;

class PoolApplicationTest {
  @Test
  void startsWithHikari() {
    verifyPool("hikari", HikariDataSource.class);
  }

  @Test
  void startsWithDruid() {
    verifyPool("druid", DruidDataSource.class);
  }

  private void verifyPool(String poolType, Class<? extends DataSource> expectedType) {
    try (ConfigurableApplicationContext context = new SpringApplicationBuilder(PoolApplication.class)
        .web(WebApplicationType.NONE)
        .properties("pool.type=" + poolType)
        .run()) {
      DataSource dataSource = context.getBean("coreDataSource", DataSource.class);
      assertEquals(expectedType, dataSource.getClass());
      assertEquals(Integer.valueOf(1),
          context.getBean(JdbcTemplate.class).queryForObject("select 1", Integer.class));
    }
  }
}
