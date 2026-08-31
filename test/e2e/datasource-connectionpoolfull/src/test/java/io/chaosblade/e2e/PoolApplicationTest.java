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
