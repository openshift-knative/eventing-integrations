#
# Copyright © 2018 Knative Authors (knative-dev@googlegroups.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

ARG JAVA_BUILDER=registry.access.redhat.com/ubi8/openjdk-21
ARG JAVA_RUNTIME=registry.access.redhat.com/ubi8/openjdk-21-runtime
ARG DEPS_IMAGE

FROM $DEPS_IMAGE AS deps

FROM $JAVA_BUILDER AS builder

USER root

WORKDIR /build

COPY --from=deps /third_party/maven/ /third_party/maven/

COPY . .

RUN mvn -Dmaven.repo.local=/third_party/maven --offline package -pl=aws-sqs-source -Drelease -am -DskipTests --no-transfer-progress

FROM $JAVA_RUNTIME AS running
ARG VERSION=""

USER 185

LABEL \
      com.redhat.component="openshift-serverless-1-eventing-integrations-aws-sqs-source-rhel8-container" \
      name="openshift-serverless-1/eventing-integrations-aws-sqs-source-rhel8" \
      version=$VERSION \
      release=$VERSION \
      summary="Red Hat OpenShift Serverless 1 Eventing Integrations AWS SQS Source" \
      maintainer="serverless-support@redhat.com" \
      description="Red Hat OpenShift Serverless 1 Eventing Integrations AWS SQS Source" \
      io.k8s.display-name="Red Hat OpenShift Serverless 1 Eventing Integrations AWS SQS Source" \
      io.k8s.description="Red Hat OpenShift Serverless Eventing Integrations AWS SQS Source" \
      io.openshift.tags=aws-sqs-source

COPY --from=builder --chown=185 /build/aws-sqs-source/target/quarkus-app/lib/ /deployments/lib/
COPY --from=builder --chown=185 /build/aws-sqs-source/target/quarkus-app/*.jar /deployments/
COPY --from=builder --chown=185 /build/aws-sqs-source/target/quarkus-app/app/ /deployments/app/
COPY --from=builder --chown=185 /build/aws-sqs-source/target/quarkus-app/quarkus/ /deployments/quarkus/

COPY LICENSE /licenses/

ENV JAVA_OPTS="-Dquarkus.http.host=0.0.0.0 -Djava.util.logging.manager=org.jboss.logmanager.LogManager"

ENTRYPOINT ["java", "-jar", "/deployments/quarkus-run.jar"]
