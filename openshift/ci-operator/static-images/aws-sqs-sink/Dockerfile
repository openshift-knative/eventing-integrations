FROM registry.access.redhat.com/ubi8/openjdk-21 as builder

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

# Install dependencies.
# consecutive builds.
RUN mvn install -am -DskipTests -Drelease -Dlicense.skip -Deditorconfig.skip --no-transfer-progress

COPY . .

RUN mvn package -pl=aws-sqs-sinke -Drelease -am -DskipTests -Deditorconfig.skip --no-transfer-progress

RUN mkdir /app && cp /build/aws-sqs-sink/target/aws-sqs-sink-1.0-SNAPSHOT.jar /app/app.jar

# We use the generated JDK from the "builder" image, so we can just go with the ubi-minimal
FROM registry.access.redhat.com/ubi8/openjdk-21-runtime as running

USER 185

COPY --from=builder /app /app
COPY LICENSE /licenses/

ENTRYPOINT ["java", "-jar", "/app/app.jar"]