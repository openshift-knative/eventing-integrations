FROM registry.access.redhat.com/ubi9/openjdk-21 as builder

WORKDIR /build

USER root

COPY pom.xml .
COPY aws-ddb-streams-source/pom.xml aws-ddb-streams-source/pom.xml
COPY aws-s3-sink/pom.xml aws-s3-sink/pom.xml
COPY aws-s3-source/pom.xml aws-s3-source/pom.xml
COPY aws-sns-sink/pom.xml aws-sns-sink/pom.xml
COPY aws-sqs-sink/pom.xml aws-sqs-sink/pom.xml
COPY aws-sqs-source/pom.xml aws-sqs-source/pom.xml
COPY log-sink/pom.xml log-sink/pom.xml
COPY timer-source/pom.xml timer-source/pom.xml
COPY tools/ tools/

RUN mvn install -am -DskipTests -Drelease -Dlicense.skip -Deditorconfig.skip --no-transfer-progress

COPY . .

RUN mvn package -pl=aws-s3-sink -Drelease -am -DskipTests -Deditorconfig.skip --no-transfer-progress

FROM registry.access.redhat.com/ubi9/openjdk-21-runtime as running

USER 185

COPY --from=builder --chown=185 /build/aws-s3-sink/target/quarkus-app/lib/ /deployments/lib/
COPY --from=builder --chown=185 /build/aws-s3-sink/target/quarkus-app/*.jar /deployments/
COPY --from=builder --chown=185 /build/aws-s3-sink/target/quarkus-app/app/ /deployments/app/
COPY --from=builder --chown=185 /build/aws-s3-sink/target/quarkus-app/quarkus/ /deployments/quarkus/

COPY LICENSE /licenses/

ENV JAVA_OPTS="-Dquarkus.http.host=0.0.0.0 -Djava.util.logging.manager=org.jboss.logmanager.LogManager"

ENTRYPOINT ["java", "-jar", "/deployments/quarkus-run.jar"]
