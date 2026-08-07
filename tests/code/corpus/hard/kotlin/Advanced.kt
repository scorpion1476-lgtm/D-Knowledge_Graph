package advanced

data class Pair(val left: Int, val right: Int)

sealed class Result {
    class Ok(val value: Int) : Result()
    class Err(val message: String) : Result()
}

fun Int.doubled(): Int {
    return this * 2
}

class Holder {
    companion object {
        fun create(): Holder {
            return Holder()
        }
    }

    suspend fun load(): Int {
        return 1
    }
}
