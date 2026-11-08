package com.familywatch.elderlocation.data

import android.content.Context

class AppContainer(context: Context) {
    val config = ConfigRepository(context)
    val database = AppDatabase.create(context)
    val tokens = SecureTokenStore(context)
    val api = ApiClient()
    val locations = LocationRepository(context, database, config, tokens, api)
}
