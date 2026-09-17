import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

/**
 * APP 版本独立于服务端与桌面端: 唯一来源是 androidapp/version.txt, versionCode 由它推导并必须单调
 * 递增 —— Android 拒绝降级覆盖安装 (见 docs/dev/android.md).
 */
val appVersionName = rootProject.file("version.txt").readText().trim()
val appVersionCode = run {
    // 必须写成三段十进制且每段 0-99: code 由它们拼出, 格式不对时宁可构建失败, 不产出无法覆盖安装的包.
    val parts = appVersionName.split('.')
    require(parts.size == 3 && parts.all { part -> part.length in 1..2 && part.all(Char::isDigit) }) {
        "androidapp/version.txt 必须是 <major>.<minor>.<patch>, 每段 0-99; 当前: $appVersionName"
    }
    val (major, minor, patch) = parts.map(String::toInt)
    major * 10_000 + minor * 100 + patch
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
        // DownloadManager 从 29 起写公共目录不需要存储权限 (见 docs/dev/android.md).
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
            // 混淆省下的体积不足以抵消崩溃栈可读性的损失.
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
    // WebSettingsCompat.setAlgorithmicDarkeningAllowed: 关闭内核的算法深色.
    implementation(libs.androidx.webkit)
    // 下拉刷新: WebView 自身没有该手势.
    implementation(libs.androidx.swiperefreshlayout)

    testImplementation(libs.junit)
    testImplementation(libs.json)
}
