module Advanced
  module ClassMethods
    def build
      new
    end
  end

  class Store
    extend ClassMethods

    class << self
      def registry
        @registry ||= {}
      end
    end

    def self.reset
      registry.clear
    end

    def each_item(&block)
      block.call
    end

    private

    def secret
      1
    end
  end
end
