import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

/**
 * APP 版本独立于服务端与桌面端: 唯一来源是 androidapp/version.txt (见 docs/dev/android.md).
 * versionCode 由 semver 推导 (三段各占两位十进制), 必须随版本单调递增 —— Android 拒绝降级覆盖安装.
 */
val appVersionName = rootProject.file("version.txt").readText().trim()
val appVersionCode = run {
    val parts = appVersionName.substringBefore('-').substringBefore('+')
        .split('.').map { it.toIntOrNull() ?: 0 }
    (parts.getOrElse(0) { 0 } * 10_000) + (parts.getOrElse(1) { 0 } * 100) + parts.getOrElse(2) { 0 }
}

// 签名配置就地读 androidapp/keystore.properties (不入库, 见 .gitignore); 缺席时 release 不签名.
val keystoreProperties = Properties().apply {
    val file = rootProject.file("keystore.properties")
    if (file.isFile) file.inputStream().use { load(it) }
}

android {
    namespace = "com.github.sqzwx.amane.android"
    compileSdk = 36

    defaultConfig {
        // 桌面壳的 bundle id 是 com.github.sqzw-x.amane; Android 的 applicationId 不允许连字符.
        applicationId = "com.github.sqzwx.amane"
        // 29 起 DownloadManager 写公共目录不再需要存储权限, 边缘到边缘与 Cookie 行为也一致.
        minSdk = 29
        targetSdk = 36
        versionCode = appVersionCode
        versionName = appVersionName
    }

    signingConfigs {
        if (keystoreProperties.isNotEmpty()) {
            create("release") {
                // storeFile 相对 androidapp/, 绝对路径原样使用.
                storeFile = rootProject.file(keystoreProperties.getProperty("storeFile"))
                storePassword = keystoreProperties.getProperty("storePassword")
                keyAlias = keystoreProperties.getProperty("keyAlias")
                keyPassword = keystoreProperties.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // 壳只有几个 Activity, 混淆省下的体积不及读崩溃栈的代价.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release")
        }
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.activity)
    // WebSettingsCompat.setAlgorithmicDarkeningAllowed: 让 prefers-color-scheme 跟随系统深色.
    implementation(libs.androidx.webkit)
    // 下拉刷新: WebView 自身没有该手势.
    implementation(libs.androidx.swiperefreshlayout)

    testImplementation(libs.junit)
    testImplementation(libs.json)
}
