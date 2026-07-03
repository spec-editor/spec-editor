plugins {
    id("java")
    id("org.jetbrains.kotlin.jvm") version "1.9.23"
    id("org.jetbrains.intellij.platform") version "2.1.0"
}

group = "com.speceditor"
version = "0.1.0"

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
    }
}

dependencies {
    intellijPlatform {
        // Target IDE: IntelliJ IDEA Community + WebStorm
        // Use "IC" for IntelliJ Community (free, supports all features we need)
        // WebStorm = IJ with JS plugin pre-installed — same base platform
        intellijIdeaCommunity("2024.2")

        // Plugin dependencies
        bundledPlugin("Git4Idea")           // Built-in git integration
        bundledPlugin("com.intellij.java")  // Java PSI support

        // Test support
        testFramework(org.jetbrains.intellij.platform.gradle.TestFrameworkType.Platform)
    }
}

kotlin {
    jvmToolchain(17)  // Minimum JDK 17 for IntelliJ 2024.2+
}

tasks {
    // Set compatibility version
    withType<JavaCompile> {
        sourceCompatibility = "17"
        targetCompatibility = "17"
    }
}
